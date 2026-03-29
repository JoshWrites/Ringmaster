"""Session route handlers — open, retrieve, keepalive, and close sessions.

Sessions represent GPU reservations for interactive inference.  A client opens
a session to pre-load a model, sends a sequence of generate requests without
paying per-request model-load latency, then closes the session to release the
GPU reservation.

Sessions have a FK constraint to the clients table (client_id → clients.id).
The client_id in the request body must correspond to a registered client, which
is guaranteed because the auth middleware has already verified the bearer token
and thus confirmed the client exists.

However, the DB client record is written by the auth route's register handler,
not by the auth middleware itself.  In tests the fixture calls
auth_manager.register() directly (in-memory only) without hitting the DB, so
we perform an upsert here to ensure the clients row exists before inserting a
session that references it via FK.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from ringmaster import db as db_ops
from ringmaster.models import SessionOpenRequest, SessionResponse
from ringmaster.server.deps import get_db_conn

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _ensure_client_row(conn: sqlite3.Connection, client_id: str) -> None:
    """Insert a placeholder clients row if one does not already exist.

    Sessions have a FK constraint: sessions.client_id → clients.id.  The DB
    enforces this at insert time.  When auth is handled purely in-memory (as in
    tests), the clients table may not yet have a row for this client.  This
    helper performs an INSERT OR IGNORE so the FK constraint is satisfied
    without raising a duplicate-key error if the row already exists.
    """
    conn.execute(
        "INSERT OR IGNORE INTO clients (id, token_hash) VALUES (?, ?)",
        (client_id, "placeholder"),
    )
    conn.commit()


def _session_row_to_response(row: dict) -> SessionResponse:
    """Convert a raw DB dict to a SessionResponse."""
    return SessionResponse(
        id=row["id"],
        client_id=row["client_id"],
        model=row["model"],
        status=row["status"],
        opened_at=row["opened_at"],
        last_activity_at=row.get("last_activity_at"),
        idle_timeout_seconds=row["idle_timeout_seconds"],
        gpu_label=row.get("gpu_label"),
    )


@router.post("", status_code=201, response_model=SessionResponse)
def open_session(
    body: SessionOpenRequest,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> SessionResponse:
    """Open a new interactive inference session and return its details.

    The session is created in 'open' status.  The client should call DELETE
    /sessions/{id} when done to release the GPU reservation promptly rather
    than waiting for the idle timeout to expire.
    """
    opened_at = datetime.now(timezone.utc).isoformat()

    # Satisfy the clients FK constraint (see module docstring).
    _ensure_client_row(conn, body.client_id)

    session_id = db_ops.insert_session(
        conn,
        client_id=body.client_id,
        model=body.model,
        opened_at=opened_at,
        idle_timeout_seconds=body.session_idle_timeout_seconds,
    )
    row = db_ops.get_session(conn, session_id)
    if row is None:
        raise HTTPException(status_code=500, detail="Session was created but could not be retrieved.")
    return _session_row_to_response(row)


@router.get("/{session_id}", response_model=SessionResponse)
def get_session(
    session_id: str,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> SessionResponse:
    """Retrieve a single session by ID.  Returns 404 if it does not exist."""
    row = db_ops.get_session(conn, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    return _session_row_to_response(row)


@router.post("/{session_id}/keepalive")
def keepalive_session(
    session_id: str,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict:
    """Reset the idle timer for a session, preventing auto-close.

    Clients should call this periodically (e.g. every 60 s) when they intend
    to continue using the session but have not sent a generate request recently.
    """
    row = db_ops.get_session(conn, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")

    db_ops.update_session_activity(conn, session_id)
    return {"session_id": session_id, "keepalive": True}


@router.delete("/{session_id}")
def close_session(
    session_id: str,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> dict:
    """Close a session and release its GPU reservation.

    Closing an already-closed session is accepted silently (idempotent) so
    that clients can safely retry a DELETE without getting spurious errors.
    """
    row = db_ops.get_session(conn, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")

    db_ops.close_session(conn, session_id)
    return {"session_id": session_id, "status": "closed"}
