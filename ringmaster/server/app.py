"""FastAPI application factory for Ringmaster.

``create_app()`` is the single entry point for constructing a fully-wired
FastAPI application.  It is called by:
  - The CLI server command (``ringmaster serve``)
  - Tests, which pass a temp config path and DB path to get a hermetically
    isolated app instance per test case.

Design decisions:
  - Factory function, not module-level singleton: this makes testing trivial
    because each call produces an independent app with its own dependencies.
    A module-level ``app = FastAPI()`` would be shared across all tests.
  - Auth middleware as a simple starlette Middleware rather than a FastAPI
    dependency: this lets us skip auth for /health globally without polluting
    every route handler with an optional auth parameter.
  - ``create_app`` returns ``(app, auth_manager)`` so the caller (CLI or test)
    has access to the AuthManager for the initial token issuance.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ringmaster import db as db_ops
from ringmaster.config import RingmasterConfig, load_config
from ringmaster.scheduler import Scheduler
from ringmaster.server.auth import AuthManager
from ringmaster.server import deps as _deps
from ringmaster.server.routes import auth, queue, sessions, status, tasks


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------


_LOCALHOST_ADDRS = frozenset({"127.0.0.1", "::1"})


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Reject requests that lack a valid bearer token.

    Design choices:
      - /health is explicitly skipped so liveness probes from external monitors
        (e.g. uptime-kuma, Kubernetes readiness checks) work without credentials.
      - Localhost connections (127.0.0.1 / ::1) skip auth entirely.  Ringmaster
        is a single-machine daemon; local clients like NetIntel should not need
        out-of-band token provisioning to use the API.
      - We read the AuthManager from the deps module rather than capturing it in
        __init__, which means the middleware always sees the current AuthManager
        even if set_deps() is called after the middleware is registered.
      - Returns 401 (not 403): the client is unauthenticated (identity unknown),
        not unauthorised (identity known but forbidden).  RFC 7235 §3.1 requires
        a WWW-Authenticate header with 401; we include a minimal one.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        """Verify the bearer token on every request except /health and localhost."""
        if request.url.path == "/health":
            return await call_next(request)

        # Local clients (same machine) skip auth — Ringmaster is single-machine.
        client_host = request.client.host if request.client else None
        if client_host in _LOCALHOST_ADDRS:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or malformed Authorization header."},
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header[len("Bearer "):]
        auth_manager = _deps.get_auth_manager()
        client_id = auth_manager.verify(token)
        if client_id is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or revoked bearer token."},
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


async def create_app(
    config_path: Path,
    db_path: Path | None = None,
) -> tuple[FastAPI, AuthManager]:
    """Construct and return a fully-wired FastAPI app plus its AuthManager.

    Steps performed:
      1. Load config from YAML at *config_path*.
      2. Open SQLite DB (WAL mode, FK enforcement).
      3. Initialise DB schema (idempotent — safe on an existing DB).
      4. Create Scheduler.
      5. Create AuthManager and load any persisted tokens from disk.
      6. Stash all singletons via deps.set_deps().
      7. Create FastAPI app.
      8. Register auth middleware.
      9. Register all route routers.

    Args:
        config_path: Path to the ringmaster.yaml config file.  An empty file
            is valid — all fields fall back to their defaults.
        db_path: Path for the SQLite database file.  Defaults to a file named
            ``ringmaster.db`` in the same directory as the config file.  Pass
            ``":memory:"`` (as a Path) or a tmp_path in tests for isolation.

    Returns:
        A tuple of ``(app, auth_manager)`` so the caller can issue the initial
        bearer token or run additional setup before the server starts.
    """
    # 1. Load config — empty YAML is fine; all fields have defaults.
    config: RingmasterConfig
    if config_path.stat().st_size == 0:
        config = RingmasterConfig()
    else:
        config = load_config(config_path)

    # 2. Determine DB path.
    if db_path is None:
        db_path = config_path.parent / "ringmaster.db"

    # 3. Create initial DB connection for schema init and Scheduler.
    #    The Scheduler owns this connection for its lifetime (background use).
    #    HTTP request handlers get per-request connections via the factory.
    conn: sqlite3.Connection = db_ops.get_db(str(db_path))
    db_ops.init_db(conn)

    # 4. Create a factory for per-request connections.
    db_factory = db_ops.get_db_factory(str(db_path))

    # 5. Create Scheduler with its own dedicated connection.
    scheduler = Scheduler(conn, config.queue)

    # 6. Create AuthManager and load persisted tokens.
    auth_manager = AuthManager()
    token_path = Path(config.auth.token_file)
    if not token_path.is_absolute():
        token_path = config_path.parent / token_path
    auth_manager.load(str(token_path))

    # 7. Wire singletons into the deps module.
    _deps.set_deps(config, db_factory, scheduler, auth_manager)

    # 8. Build FastAPI app.
    app = FastAPI(
        title="Ringmaster",
        description="GPU workstation AI task orchestrator for home networks.",
        version="0.1.0",
    )

    # 9. Auth middleware — runs before every request handler.
    app.add_middleware(BearerAuthMiddleware)

    # 10. Register routers.
    app.include_router(tasks.router)
    app.include_router(sessions.router)
    app.include_router(queue.router)
    app.include_router(status.router)
    app.include_router(auth.router)

    return app, auth_manager
