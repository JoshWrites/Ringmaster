"""SQLite database layer for Ringmaster.

All persistent state (task queue, sessions, clients, power event log) lives in
a single SQLite file.  We deliberately avoid an ORM to keep the dependency
footprint small and to make the SQL explicit and auditable.

Design decisions:
  - WAL journal mode: allows concurrent readers while a writer is active.
    This matters because the HTTP API and the background scheduler both access
    the database; WAL prevents the scheduler from blocking API reads.
  - Row factory set to sqlite3.Row: lets callers address columns by name
    (row["status"]) instead of index (row[0]), making the code self-documenting.
  - Timestamps stored as ISO 8601 strings: SQLite has no native datetime type;
    ISO 8601 strings sort lexicographically which preserves chronological order,
    making range queries and ORDER BY correct without conversion.
  - Metadata stored as JSON text: avoids a separate metadata table while still
    allowing structured storage.
  - uuid4 IDs: random UUIDs are unpredictable, which prevents enumeration of
    task or session IDs by external clients.

Queue ordering logic (get_next_queued_task):
  1. Lower priority number = higher urgency, so ORDER BY priority ASC.
  2. Tasks with a deadline are more urgent than tasks without one, so
     NULL deadlines sort last (CASE WHEN deadline IS NULL THEN 1 ELSE 0 END).
  3. Within the same priority+deadline bucket, FIFO via submitted_at ASC.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------


def get_db(path: str) -> sqlite3.Connection:
    """Open a SQLite database at *path* and return a configured connection.

    Pass ``":memory:"`` for an in-memory database (useful in tests).

    The connection is configured with:
      - WAL journal mode for concurrent read access.
      - Row factory set to sqlite3.Row for named-column access.
      - Foreign key enforcement enabled.

    Args:
        path: Filesystem path to the SQLite database file, or ``":memory:"``.

    Returns:
        An open, configured sqlite3.Connection.
    """
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes if they do not already exist.

    Safe to call on an existing database — uses CREATE TABLE IF NOT EXISTS so
    it is idempotent.  The schema version is intentionally not tracked here;
    migrations are out of scope for the initial implementation.

    Args:
        conn: An open database connection returned by get_db().
    """
    conn.executescript("""
        -- Registered API clients.  The token is stored as a hash so that a
        -- database dump does not expose live credentials.
        CREATE TABLE IF NOT EXISTS clients (
            id          TEXT PRIMARY KEY,
            token_hash  TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- Background tasks submitted by clients.
        --
        -- priority:         1 = highest urgency, 5 = lowest urgency.
        -- status:           queued → running → completed | failed | cancelled.
        -- unattended_policy: 'run' | 'defer' | 'notify'.
        -- metadata_json:    client-supplied key-value pairs serialised as JSON.
        CREATE TABLE IF NOT EXISTS tasks (
            id                  TEXT PRIMARY KEY,
            task_type           TEXT NOT NULL,
            model               TEXT NOT NULL,
            client_id           TEXT NOT NULL,
            status              TEXT NOT NULL DEFAULT 'queued',
            priority            INTEGER NOT NULL DEFAULT 3,
            deadline            TEXT,
            prompt              TEXT,
            callback_url        TEXT,
            unattended_policy   TEXT NOT NULL DEFAULT 'run',
            metadata_json       TEXT NOT NULL DEFAULT '{}',
            submitted_at        TEXT NOT NULL,
            started_at          TEXT,
            completed_at        TEXT,
            result              TEXT,
            error               TEXT,
            gpu_used            TEXT,
            duration_seconds    REAL,
            FOREIGN KEY (client_id) REFERENCES clients(id)
        );

        -- Index to accelerate queue polling.  The scheduler calls
        -- get_next_queued_task() on every dispatch cycle; without this index
        -- that query would do a full table scan on large queues.
        CREATE INDEX IF NOT EXISTS idx_tasks_queue
            ON tasks (status, priority, deadline, submitted_at);

        -- Index to accelerate list_tasks() filtered by client.
        CREATE INDEX IF NOT EXISTS idx_tasks_client
            ON tasks (client_id, status);

        -- Interactive inference sessions.  A session reserves a GPU for a
        -- sequence of generate requests without reloading the model each time.
        --
        -- status: 'open' | 'closed' | 'expired'.
        CREATE TABLE IF NOT EXISTS sessions (
            id                   TEXT PRIMARY KEY,
            client_id            TEXT NOT NULL,
            model                TEXT NOT NULL,
            status               TEXT NOT NULL DEFAULT 'open',
            opened_at            TEXT NOT NULL,
            last_activity_at     TEXT,
            idle_timeout_seconds INTEGER NOT NULL DEFAULT 600,
            gpu_label            TEXT,
            FOREIGN KEY (client_id) REFERENCES clients(id)
        );

        -- Index for active-session lookups used by the idle-timeout reaper.
        CREATE INDEX IF NOT EXISTS idx_sessions_open
            ON sessions (status, last_activity_at);

        -- Audit log of power lifecycle events (sleep, wake, shutdown).
        -- Stored as a plain append-only log; no updates or deletes.
        CREATE TABLE IF NOT EXISTS power_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type  TEXT NOT NULL,
            source      TEXT,
            detail      TEXT,
            recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Convert a sqlite3.Row to a plain dict, or return None if row is None."""
    if row is None:
        return None
    return dict(row)


# ---------------------------------------------------------------------------
# Task operations
# ---------------------------------------------------------------------------


def insert_task(
    conn: sqlite3.Connection,
    *,
    task_type: str,
    model: str,
    client_id: str,
    submitted_at: str,
    priority: int = 3,
    deadline: str | None = None,
    prompt: str | None = None,
    callback_url: str | None = None,
    unattended_policy: str = "run",
    metadata: dict[str, Any] | None = None,
) -> str:
    """Insert a new task into the queue and return its generated ID.

    The task is created in 'queued' status.  The scheduler picks it up via
    get_next_queued_task() according to priority/deadline/FIFO ordering.

    Args:
        conn: Open database connection.
        task_type: Kind of task, e.g. 'generate' or 'embed'.
        model: Ollama model tag, e.g. 'llama3:8b'.
        client_id: ID of the client submitting the task.
        submitted_at: ISO 8601 UTC timestamp of submission.
        priority: Queue priority 1–5; lower numbers are dispatched first.
        deadline: Optional ISO 8601 UTC deadline.  Tasks with a deadline are
            dequeued before tasks without one within the same priority level.
        prompt: Input text for generation/embedding tasks.
        callback_url: URL to POST a webhook payload to on completion.
        unattended_policy: Behaviour when the user is present; one of
            'run', 'defer', or 'notify'.
        metadata: Arbitrary client-supplied key-value pairs.

    Returns:
        The UUID4 string ID assigned to the new task.
    """
    task_id = str(uuid4())
    metadata_json = json.dumps(metadata or {})
    conn.execute(
        """
        INSERT INTO tasks (
            id, task_type, model, client_id, submitted_at,
            priority, deadline, prompt, callback_url,
            unattended_policy, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id, task_type, model, client_id, submitted_at,
            priority, deadline, prompt, callback_url,
            unattended_policy, metadata_json,
        ),
    )
    conn.commit()
    return task_id


def get_task(conn: sqlite3.Connection, task_id: str) -> dict[str, Any] | None:
    """Fetch a single task by ID.

    Args:
        conn: Open database connection.
        task_id: UUID4 string ID of the task.

    Returns:
        A dict of column name → value, or None if no task with that ID exists.
    """
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_dict(row)


def update_task_status(conn: sqlite3.Connection, task_id: str, status: str) -> None:
    """Set the status field of a task without touching other fields.

    Use this for simple lifecycle transitions like 'queued' → 'cancelled'.
    For richer transitions use update_task_started() or update_task_completed().

    Args:
        conn: Open database connection.
        task_id: ID of the task to update.
        status: New status string.
    """
    conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
    conn.commit()


def update_task_started(conn: sqlite3.Connection, task_id: str) -> None:
    """Mark a task as running and record when it started.

    Called by the worker immediately before it dispatches the task to Ollama.

    Args:
        conn: Open database connection.
        task_id: ID of the task being started.
    """
    conn.execute(
        "UPDATE tasks SET status = 'running', started_at = ? WHERE id = ?",
        (_utc_now(), task_id),
    )
    conn.commit()


def update_task_completed(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    result: str | None = None,
    error: str | None = None,
    gpu_used: str | None = None,
    duration: float | None = None,
) -> None:
    """Mark a task as completed or failed and record its outcome.

    If *error* is provided the status is set to 'failed'; otherwise 'completed'.
    Both result and error can be None for task types that produce no output.

    Args:
        conn: Open database connection.
        task_id: ID of the task to finalise.
        result: Output text on success.
        error: Error message on failure.
        gpu_used: Label of the GPU that ran the task.
        duration: Wall-clock seconds from start to finish.
    """
    final_status = "failed" if error else "completed"
    conn.execute(
        """
        UPDATE tasks
        SET status = ?, completed_at = ?, result = ?,
            error = ?, gpu_used = ?, duration_seconds = ?
        WHERE id = ?
        """,
        (final_status, _utc_now(), result, error, gpu_used, duration, task_id),
    )
    conn.commit()


def list_tasks(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    client_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return a list of tasks, optionally filtered by status and/or client.

    Results are ordered by submitted_at descending (most recent first) so
    that API consumers see the latest tasks at the top.

    Args:
        conn: Open database connection.
        status: If provided, return only tasks with this status.
        client_id: If provided, return only tasks for this client.
        limit: Maximum number of tasks to return.

    Returns:
        A list of task dicts.  May be empty if no tasks match.
    """
    clauses: list[str] = []
    params: list[Any] = []

    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if client_id is not None:
        clauses.append("client_id = ?")
        params.append(client_id)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)

    rows = conn.execute(
        f"SELECT * FROM tasks {where} ORDER BY submitted_at DESC LIMIT ?",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def get_next_queued_task(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Return the highest-priority queued task without removing it from the DB.

    Ordering rules (applied in this exact priority):
      1. priority ASC — lower number = more urgent.
      2. Deadline present before deadline absent — tasks with a deadline are
         more time-sensitive than open-ended tasks at the same priority level.
      3. submitted_at ASC — FIFO tiebreak within identical priority+deadline.

    The task is NOT marked as running here.  The caller must call
    update_task_started() after deciding to execute it, so that a crash
    between get_next_queued_task() and the actual dispatch does not silently
    lose the task.

    Args:
        conn: Open database connection.

    Returns:
        The next task dict, or None if the queue is empty.
    """
    row = conn.execute(
        """
        SELECT * FROM tasks
        WHERE status = 'queued'
        ORDER BY
            priority ASC,
            CASE WHEN deadline IS NULL THEN 1 ELSE 0 END ASC,
            deadline ASC,
            submitted_at ASC
        LIMIT 1
        """,
    ).fetchone()
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Session operations
# ---------------------------------------------------------------------------


def insert_session(
    conn: sqlite3.Connection,
    *,
    client_id: str,
    model: str,
    opened_at: str,
    idle_timeout_seconds: int = 600,
    gpu_label: str | None = None,
) -> str:
    """Create a new interactive inference session and return its ID.

    The session is created in 'open' status with no last_activity_at, which
    the idle-timeout reaper treats as a session that has never been used.

    Args:
        conn: Open database connection.
        client_id: ID of the client opening the session.
        model: Ollama model tag to pre-load for this session.
        opened_at: ISO 8601 UTC timestamp when the session was opened.
        idle_timeout_seconds: Inactivity timeout before auto-close.
        gpu_label: GPU reserved for this session (may be assigned later).

    Returns:
        The UUID4 string ID assigned to the new session.
    """
    session_id = str(uuid4())
    conn.execute(
        """
        INSERT INTO sessions (id, client_id, model, opened_at, idle_timeout_seconds, gpu_label)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (session_id, client_id, model, opened_at, idle_timeout_seconds, gpu_label),
    )
    conn.commit()
    return session_id


def get_session(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    """Fetch a single session by ID.

    Args:
        conn: Open database connection.
        session_id: UUID4 string ID of the session.

    Returns:
        A dict of column name → value, or None if no session with that ID exists.
    """
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return _row_to_dict(row)


def update_session_activity(conn: sqlite3.Connection, session_id: str) -> None:
    """Record that activity just occurred on a session, resetting its idle timer.

    Called by the worker after each successful generate on a session.

    Args:
        conn: Open database connection.
        session_id: ID of the session that just received a request.
    """
    conn.execute(
        "UPDATE sessions SET last_activity_at = ? WHERE id = ?",
        (_utc_now(), session_id),
    )
    conn.commit()


def close_session(conn: sqlite3.Connection, session_id: str) -> None:
    """Mark a session as closed and release its GPU reservation.

    Called either explicitly by the client (DELETE /sessions/{id}) or
    automatically by the idle-timeout reaper.

    Args:
        conn: Open database connection.
        session_id: ID of the session to close.
    """
    conn.execute(
        "UPDATE sessions SET status = 'closed' WHERE id = ?",
        (session_id,),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Client operations
# ---------------------------------------------------------------------------


def insert_client(conn: sqlite3.Connection, *, client_id: str, token_hash: str) -> None:
    """Register a new API client.

    The token is stored as a hash (the caller is responsible for hashing)
    so that a database dump does not expose live bearer tokens.

    Args:
        conn: Open database connection.
        client_id: Opaque string identifier for the client.
        token_hash: Hash of the client's bearer token.
    """
    conn.execute(
        "INSERT INTO clients (id, token_hash) VALUES (?, ?)",
        (client_id, token_hash),
    )
    conn.commit()


def get_client_by_id(conn: sqlite3.Connection, client_id: str) -> dict[str, Any] | None:
    """Look up a client by their ID.

    Args:
        conn: Open database connection.
        client_id: Opaque string identifier to look up.

    Returns:
        A dict with client fields, or None if no client with that ID exists.
    """
    row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Power event log
# ---------------------------------------------------------------------------


def log_power_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    source: str | None = None,
    detail: str | None = None,
) -> None:
    """Append a power lifecycle event to the audit log.

    Events are append-only — they are never updated or deleted.  The log is
    used for post-hoc analysis of sleep/wake patterns and to diagnose cases
    where the workstation slept unexpectedly while tasks were queued.

    Args:
        conn: Open database connection.
        event_type: Short string identifying the event, e.g. 'sleep', 'wake',
            'shutdown', 'wol_sent'.
        source: Component that triggered the event, e.g. 'scheduler', 'api'.
        detail: Human-readable description for the audit log, e.g.
            'task task-123 required GPU'.
    """
    conn.execute(
        "INSERT INTO power_events (event_type, source, detail) VALUES (?, ?, ?)",
        (event_type, source, detail),
    )
    conn.commit()
