"""Queue state machine for Ringmaster.

The Scheduler owns all transitions in the task lifecycle that originate from
the queue layer rather than from task execution results.  It is intentionally
*not* async — all DB operations are synchronous SQLite calls and the state
machine logic is pure Python.  The async dispatch loop in the worker layer
calls into the Scheduler synchronously from its own thread.

Design decisions:
  - Pause/resume/drain are in-memory flags, not DB state.  They survive a
    crash only as long as the process does.  On restart the scheduler boots in
    the running state and picks up any queued tasks, which is the safe default.
  - ``drain()`` distinguishes two cases:
      1. No task running → pause immediately (nothing in-flight to protect).
      2. Task running → set ``_draining`` flag; ``on_task_completed()`` will
         trigger the pause once the current task finishes cleanly.
    This design avoids interrupting a task mid-execution just because an admin
    requested a drain.
  - ``cancel_current()`` uses the status ``'interrupted'`` rather than
    ``'cancelled'`` or ``'failed'``:
      - ``'cancelled'`` means the client cancelled before dispatch.
      - ``'failed'`` means the worker encountered an error.
      - ``'interrupted'`` means the scheduler stopped it externally (power
        event, admin action) — a distinct cause that is useful for auditing.
  - ``queue_depth()`` counts only ``'queued'`` rows.  Running, completed,
    failed, deferred, and interrupted tasks are not backlog and must not
    consume capacity.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone

from ringmaster import db
from ringmaster.config import QueueConfig


# ---------------------------------------------------------------------------
# Public exceptions
# ---------------------------------------------------------------------------


class QueueFullError(Exception):
    """Raised by submit_task() when the queue has reached max_queue_depth.

    The HTTP API layer catches this and returns HTTP 429 (Too Many Requests)
    so that clients know to back off and retry.
    """


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class Scheduler:
    """Queue state machine: accepts tasks, tracks running state, and controls
    the pause/drain lifecycle.

    The Scheduler does *not* execute tasks — that is the worker's job.  It
    provides the policy layer: what is the next task to run, is dispatch
    allowed right now, and what happens when a task finishes?

    Args:
        conn: An open aiosqlite (or synchronous sqlite3) connection that has
            been initialised with ``db.init_db()``.
        config: QueueConfig instance, typically loaded from ringmaster.yaml.
    """

    def __init__(self, conn: sqlite3.Connection, config: QueueConfig) -> None:
        self._conn = conn
        self._config = config
        self._lock = threading.Lock()

        # In-memory state flags — not persisted to DB (see module docstring).
        self._paused: bool = False
        self._draining: bool = False
        self._current_task_id: str | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_paused(self) -> bool:
        """True when the scheduler is paused and will not dispatch new tasks."""
        with self._lock:
            return self._paused

    @property
    def is_draining(self) -> bool:
        """True when drain() was called while a task was running.

        In this state the scheduler allows the current task to finish, then
        pauses automatically via on_task_completed().
        """
        with self._lock:
            return self._draining

    @property
    def current_task_id(self) -> str | None:
        """The ID of the task currently being dispatched, or None if idle."""
        with self._lock:
            return self._current_task_id

    # ------------------------------------------------------------------
    # Queue operations
    # ------------------------------------------------------------------

    def submit_task(
        self,
        task_type: str,
        model: str,
        prompt: str | None,
        priority: int | None,
        client_id: str,
        callback_url: str | None = None,
        unattended_policy: str = "run",
        deadline: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Validate, persist, and enqueue a new task.

        The task is written to the database in ``'queued'`` status so that it
        survives a scheduler restart.  The queue depth check runs *before* the
        insert to avoid writing a row that would immediately violate the limit.

        Args:
            task_type: Kind of work, e.g. ``'generate'`` or ``'embed'``.
            model: Ollama model tag, e.g. ``'llama3:8b'``.
            prompt: Input text for the task (may be None for non-text tasks).
            priority: Urgency 1–5; lower is more urgent.  Pass None to use
                ``config.default_priority``.
            client_id: Registered client submitting this task.
            callback_url: Optional URL to receive a webhook on completion.
            unattended_policy: One of ``'run'``, ``'defer'``, or ``'notify'``.
            deadline: Optional ISO 8601 UTC deadline string.
            metadata: Arbitrary client-supplied key-value pairs.

        Returns:
            The UUID4 string ID assigned to the new task.

        Raises:
            QueueFullError: When ``queue_depth() >= config.max_queue_depth``.
        """
        with self._lock:
            if self.queue_depth() >= self._config.max_queue_depth:
                raise QueueFullError(
                    f"Queue is full ({self._config.max_queue_depth} tasks waiting). "
                    "Retry after existing tasks are processed."
                )

            # Substitute the configured default when the caller omits a priority.
            effective_priority = priority if priority is not None else self._config.default_priority

            submitted_at = datetime.now(timezone.utc).isoformat()

            task_id = db.insert_task(
                self._conn,
                task_type=task_type,
                model=model,
                client_id=client_id,
                submitted_at=submitted_at,
                priority=effective_priority,
                deadline=deadline,
                prompt=prompt,
                callback_url=callback_url,
                unattended_policy=unattended_policy,
                metadata=metadata,
            )
            return task_id

    def next_task(self) -> dict | None:
        """Return the highest-priority queued task, or None if dispatch is blocked.

        Returns None in two cases:
          1. The scheduler is paused (pause() or drain() was called).
          2. The queue contains no tasks in ``'queued'`` status.

        The returned task is *not* marked as running — the caller must call
        ``set_current(task_id)`` and then ``db.update_task_started()`` before
        dispatching to Ollama.  This separation ensures a crash between
        next_task() and the actual dispatch does not silently lose the task.

        Returns:
            A task dict (column name → value), or None.
        """
        with self._lock:
            if self._paused:
                return None
            return db.get_next_queued_task(self._conn)

    def set_current(self, task_id: str) -> None:
        """Record that *task_id* is the task currently being executed.

        The worker calls this immediately after deciding to dispatch a task and
        before making any Ollama API call.  Having a single authoritative
        ``current_task_id`` allows cancel_current() and drain() to act on the
        right task even if the worker and scheduler are in different threads.

        Args:
            task_id: ID of the task about to be dispatched.
        """
        with self._lock:
            self._current_task_id = task_id

    def on_task_completed(self) -> None:
        """Called by the worker when the current task finishes (success or failure).

        Clears the current task slot.  If ``drain()`` was called while the task
        was running, this method triggers the deferred pause so the scheduler
        stops accepting new work.

        The worker is responsible for writing the outcome (result/error) to the
        DB before calling this method.
        """
        with self._lock:
            self._current_task_id = None

            # Honour a pending drain request now that the in-flight task is done.
            if self._draining:
                self._draining = False
                self._paused = True

    def queue_depth(self) -> int:
        """Return the number of tasks currently in ``'queued'`` status.

        Only ``'queued'`` tasks count — running, completed, failed, deferred,
        and interrupted tasks are not backlog and must not consume capacity.

        Returns:
            Non-negative integer count of queued tasks.
        """
        row = self._conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status = 'queued'"
        ).fetchone()
        return row[0]

    def defer_task(self, task_id: str) -> None:
        """Move a task to ``'deferred'`` status, removing it from active dispatch.

        Deferred tasks are excluded from next_task() results and must be
        explicitly approved (via approve_task()) before they will run.  This
        is the mechanism used to implement the ``unattended_policy='defer'``
        behaviour: tasks arrive while the user is active, are deferred pending
        approval, and run later when the user approves or goes idle.

        Args:
            task_id: ID of the task to defer.
        """
        with self._lock:
            db.update_task_status(self._conn, task_id, "deferred")

    def approve_task(self, task_id: str) -> None:
        """Move a deferred task back to ``'queued'`` status.

        The task re-enters normal dispatch as if it had just been submitted,
        preserving its original priority and deadline so the ordering is
        maintained correctly.

        Args:
            task_id: ID of the task to approve.
        """
        with self._lock:
            db.update_task_status(self._conn, task_id, "queued")

    # ------------------------------------------------------------------
    # Lifecycle controls
    # ------------------------------------------------------------------

    def pause(self) -> None:
        """Stop dispatching new tasks immediately.

        Any in-flight task continues to completion; only the selection of
        *new* tasks from the queue is halted.  Call resume() to restart
        dispatch.
        """
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        """Resume task dispatch after a pause or drain.

        Clears both the paused and draining flags so the scheduler returns to
        normal operation.  If a drain was pending (draining=True, paused=True),
        resume() cancels the drain entirely.
        """
        with self._lock:
            self._paused = False
            self._draining = False

    def drain(self) -> None:
        """Gracefully quiesce the scheduler.

        Two cases:
          - No task running: pause immediately (nothing in-flight to protect).
          - Task running: set the draining flag; on_task_completed() will
            trigger the pause once the current task finishes.

        Use drain() before a planned power event (sleep, shutdown) to ensure
        no task is interrupted mid-execution.
        """
        with self._lock:
            if self._current_task_id is None:
                # Safe to pause immediately — no task in flight.
                self._paused = True
            else:
                # Let the current task finish, then pause in on_task_completed().
                self._draining = True

    def cancel_current(self) -> str | None:
        """Interrupt the currently running task and clear the current task slot.

        Marks the task ``'interrupted'`` in the DB (not ``'cancelled'`` or
        ``'failed'`` — see module docstring for the distinction).  The worker
        is responsible for actually stopping the Ollama request; this method
        only updates the scheduler and DB state.

        Returns:
            The ID of the cancelled task, or None if no task was running.
        """
        with self._lock:
            if self._current_task_id is None:
                return None

            task_id = self._current_task_id
            db.update_task_status(self._conn, task_id, "interrupted")
            self._current_task_id = None
            return task_id
