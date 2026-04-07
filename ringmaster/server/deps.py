"""Dependency injection singletons for the Ringmaster FastAPI application.

FastAPI's ``Depends()`` mechanism wires these getter functions into route
handlers, so routes never import global state directly — they receive their
dependencies through function parameters.  This makes routes trivially testable
(swap the singletons via ``set_deps`` in test setup) and avoids circular imports
between the app factory and the route modules.

Lifecycle:
  1. ``create_app()`` in app.py constructs all dependencies (config, DB, etc.).
  2. ``create_app()`` calls ``set_deps(...)`` once to stash them here.
  3. Route handlers declare ``dep: T = Depends(get_X)`` — FastAPI calls the
     getter and injects the singleton for every request.

DB connections are per-request: each call to ``get_db_conn()`` creates a new
connection from the factory and yields it as a FastAPI dependency. The
connection is closed after the request completes.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Generator

from ringmaster.config import RingmasterConfig
from ringmaster.scheduler import Scheduler
from ringmaster.server.auth import AuthManager

# ---------------------------------------------------------------------------
# Module-level singletons (set once at startup by create_app)
# ---------------------------------------------------------------------------

_config: RingmasterConfig | None = None
_db_factory: Callable[[], sqlite3.Connection] | None = None
_scheduler: Scheduler | None = None
_auth_manager: AuthManager | None = None


def set_deps(
    config: RingmasterConfig,
    db_factory: Callable[[], sqlite3.Connection],
    scheduler: Scheduler,
    auth: AuthManager,
) -> None:
    """Stash the application-level singletons for injection into route handlers.

    Called exactly once by ``create_app()`` after all dependencies have been
    constructed and initialised.  Calling this a second time replaces the
    singletons — useful in tests that create a fresh app per test case.

    Args:
        config: The validated RingmasterConfig loaded from YAML.
        db_factory: A callable that returns a new configured SQLite connection.
        scheduler: The Scheduler instance managing the task queue state machine.
        auth: The AuthManager holding the bearer-token registry.
    """
    global _config, _db_factory, _scheduler, _auth_manager
    _config = config
    _db_factory = db_factory
    _scheduler = scheduler
    _auth_manager = auth


# ---------------------------------------------------------------------------
# Getter functions — used as FastAPI Depends() targets
# ---------------------------------------------------------------------------


def get_config() -> RingmasterConfig:
    """Return the application config singleton.

    Raises:
        RuntimeError: If ``set_deps()`` has not been called yet.
    """
    if _config is None:
        raise RuntimeError("Dependencies not initialised — call set_deps() first.")
    return _config


def get_db_conn() -> Generator[sqlite3.Connection, None, None]:
    """Yield a fresh SQLite connection for this request, closing it afterward.

    Each HTTP request gets its own connection to avoid thread-safety issues
    with concurrent access to a shared connection.  The connection is closed
    in the finally block after FastAPI finishes processing the request.

    Yields:
        A new, configured sqlite3.Connection.

    Raises:
        RuntimeError: If ``set_deps()`` has not been called yet.
    """
    if _db_factory is None:
        raise RuntimeError("Dependencies not initialised — call set_deps() first.")
    conn = _db_factory()
    try:
        yield conn
    finally:
        conn.close()


def get_scheduler() -> Scheduler:
    """Return the Scheduler singleton.

    Raises:
        RuntimeError: If ``set_deps()`` has not been called yet.
    """
    if _scheduler is None:
        raise RuntimeError("Dependencies not initialised — call set_deps() first.")
    return _scheduler


def get_auth_manager() -> AuthManager:
    """Return the AuthManager singleton.

    Raises:
        RuntimeError: If ``set_deps()`` has not been called yet.
    """
    if _auth_manager is None:
        raise RuntimeError("Dependencies not initialised — call set_deps() first.")
    return _auth_manager
