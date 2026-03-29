"""Task queue route handlers — submit, list, retrieve, and control tasks.

The task lifecycle is:
  queued → running → completed | failed | interrupted
                ↑
  deferred ────┘  (via approve)

Clients submit tasks via POST /tasks, poll them via GET /tasks/{id}, and can
request cancellation or deferral as needed.  The Scheduler owns the actual
state-machine transitions; these routes are thin HTTP adapters around it.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ringmaster import db as db_ops
from ringmaster.models import TaskResponse, TaskSubmitRequest
from ringmaster.scheduler import QueueFullError, Scheduler
from ringmaster.server.deps import get_db_conn, get_scheduler

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _ensure_client_row(conn: sqlite3.Connection, client_id: str) -> None:
    """Insert a placeholder clients row if one does not already exist.

    Tasks have a FK constraint: tasks.client_id → clients.id.  In tests the
    AuthManager operates purely in-memory (no DB row is written at register
    time), so we guarantee the FK is satisfied via INSERT OR IGNORE here rather
    than requiring the caller to pre-create the client row.
    """
    conn.execute(
        "INSERT OR IGNORE INTO clients (id, token_hash) VALUES (?, ?)",
        (client_id, "placeholder"),
    )
    conn.commit()


def _task_row_to_response(row: dict) -> TaskResponse:
    """Convert a raw DB dict to a TaskResponse, handling the JSON metadata field.

    The DB stores metadata as a JSON string (metadata_json); the API model
    expects a plain dict.  We perform the conversion here so route handlers
    don't need to know about the storage representation.
    """
    import json

    meta_raw = row.get("metadata_json") or "{}"
    metadata = json.loads(meta_raw) if isinstance(meta_raw, str) else (meta_raw or {})
    return TaskResponse(
        id=row["id"],
        task_type=row["task_type"],
        model=row["model"],
        priority=row["priority"],
        status=row["status"],
        client_id=row["client_id"],
        submitted_at=row["submitted_at"],
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        deadline=row.get("deadline"),
        prompt=row.get("prompt"),
        result=row.get("result"),
        error=row.get("error"),
        gpu_used=row.get("gpu_used"),
        duration_seconds=row.get("duration_seconds"),
        callback_url=row.get("callback_url"),
        unattended_policy=row.get("unattended_policy", "run"),
        metadata=metadata,
    )


@router.post("", status_code=201, response_model=TaskResponse)
def submit_task(
    body: TaskSubmitRequest,
    scheduler: Scheduler = Depends(get_scheduler),
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> TaskResponse:
    """Submit a new background task to the queue.

    The Scheduler validates the queue depth and persists the task atomically.
    Returns 429 if the queue is full so clients know to back off and retry.
    """
    # Satisfy the clients FK constraint before inserting the task row.
    _ensure_client_row(conn, body.client_id)

    try:
        task_id = scheduler.submit_task(
            task_type=body.task_type,
            model=body.model,
            prompt=body.prompt,
            priority=body.priority,
            client_id=body.client_id,
            callback_url=body.callback_url,
            unattended_policy=body.unattended_policy,
            deadline=body.deadline,
            metadata=body.metadata,
        )
    except QueueFullError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    # Re-fetch from DB to get all defaults filled in (submitted_at, status, etc.)
    row = db_ops.get_task(conn, task_id)
    if row is None:
        raise HTTPException(status_code=500, detail="Task was created but could not be retrieved.")
    return _task_row_to_response(row)


@router.get("", response_model=list[TaskResponse])
def list_tasks(
    status: str | None = None,
    client_id: str | None = None,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> list[TaskResponse]:
    """List tasks, optionally filtered by status and/or client_id.

    Returns tasks most-recent-first (by submitted_at desc) with a default
    limit of 100.  Pass query params to narrow the results.
    """
    rows = db_ops.list_tasks(conn, status=status, client_id=client_id)
    return [_task_row_to_response(row) for row in rows]


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(
    task_id: str,
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> TaskResponse:
    """Retrieve a single task by ID.  Returns 404 if the task does not exist."""
    row = db_ops.get_task(conn, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    return _task_row_to_response(row)


@router.post("/current/cancel")
def cancel_current_task(
    scheduler: Scheduler = Depends(get_scheduler),
) -> dict:
    """Interrupt the currently running task and mark it 'interrupted'.

    Returns the ID of the cancelled task, or null if no task was running.
    This is a best-effort operation — the worker is responsible for actually
    stopping the Ollama request; this endpoint only updates scheduler state.
    """
    cancelled_id = scheduler.cancel_current()
    return {"cancelled_task_id": cancelled_id}


@router.post("/{task_id}/approve")
def approve_task(
    task_id: str,
    scheduler: Scheduler = Depends(get_scheduler),
) -> dict:
    """Move a deferred task back to 'queued' status so it re-enters dispatch.

    Used to approve tasks that were deferred because the user was present when
    they arrived and the task had unattended_policy='defer'.
    """
    scheduler.approve_task(task_id)
    return {"task_id": task_id, "status": "queued"}


@router.post("/{task_id}/defer")
def defer_task(
    task_id: str,
    scheduler: Scheduler = Depends(get_scheduler),
) -> dict:
    """Move a task to 'deferred' status, removing it from active dispatch.

    The task will remain deferred until explicitly approved via /tasks/{id}/approve.
    """
    scheduler.defer_task(task_id)
    return {"task_id": task_id, "status": "deferred"}
