"""Background task execution engine for Ringmaster.

The Worker is the core execution engine.  It dequeues one task at a time from
the Scheduler, calls Ollama to run inference, persists the outcome, releases the
sleep inhibitor, and fires a webhook to notify the caller.

Design decisions:
  - run_one() processes exactly one task per call.  The dispatch loop in the
    application layer calls run_one() repeatedly.  Keeping single-task
    granularity makes the execution unit easy to test and reason about.
  - The inhibitor is acquired *before* calling Ollama and released in a
    finally block so it is always released, even if Ollama raises or the
    webhook delivery fails.  A leaked inhibitor lock would permanently prevent
    the workstation from sleeping.
  - OllamaError is caught explicitly and treated as a task failure rather than
    a crash so one bad request (e.g. model not found) does not kill the loop.
  - duration is measured with time.monotonic() rather than wall-clock time to
    avoid skewing the measurement if the system clock is adjusted mid-task.
  - deliver_webhook is called *after* releasing the inhibitor so that a slow
    webhook receiver does not extend the sleep-blocking window.
  - The deliver_webhook argument is an async callable rather than the module
    function directly; this makes it trivially mockable in tests without
    patching the module namespace.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

import sqlite3

from ringmaster import db
from ringmaster.models import WebhookPayload
from ringmaster.ollama import OllamaClient, OllamaError
from ringmaster.power.inhibitor import SleepInhibitor
from ringmaster.scheduler import Scheduler


class Worker:
    """Execute one queued task per run_one() call.

    Ties together the Scheduler (what to run), OllamaClient (how to run it),
    SleepInhibitor (keep the workstation awake during inference), and webhook
    delivery (notify the caller when the task finishes).

    Args:
        conn: Open aiosqlite-compatible (or sync sqlite3) connection used for
            task status updates.  Must have been initialised with db.init_db().
        scheduler: Scheduler instance that controls dispatch order and lifecycle.
        ollama: OllamaClient used to run inference.
        inhibitor: SleepInhibitor that prevents sleep while a task is running.
        deliver_webhook: Async callable with signature
            ``(url: str | None, payload: WebhookPayload) -> bool``.
            Injected rather than imported directly to simplify testing.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        scheduler: Scheduler,
        ollama: OllamaClient,
        inhibitor: SleepInhibitor,
        deliver_webhook: Callable[[str | None, WebhookPayload], Awaitable[bool]],
    ) -> None:
        self._conn = conn
        self._scheduler = scheduler
        self._ollama = ollama
        self._inhibitor = inhibitor
        self._deliver_webhook = deliver_webhook

    async def run_one(self) -> bool:
        """Dequeue and execute one task, then fire its webhook.

        The full execution sequence for a single task:
          1. Ask the scheduler for the next task; return False if none available.
          2. Acquire the sleep inhibitor so the workstation stays awake.
          3. Tell the scheduler which task is now in-flight.
          4. Mark the task as 'running' in the database.
          5. Call Ollama to generate the response.
          6. On OllamaError: capture the error message.
          7. On success: capture the result text.
          8. Record the wall-clock duration.
          9. Persist the outcome (completed/failed) to the database.
         10. Release the sleep inhibitor.
         11. Tell the scheduler the task is finished (triggers drain if pending).
         12. Fire the webhook payload to the task's callback_url.
         13. Return True to signal that a task was processed.

        Returns:
            True if a task was found and processed (regardless of whether the
            task itself succeeded or failed).  False if the scheduler had no
            task to dispatch (empty queue, paused, etc.).
        """
        task = self._scheduler.next_task()
        if task is None:
            # Queue is empty or scheduler is paused — nothing to do right now.
            return False

        task_id: str = task["id"]
        model: str = task["model"]
        prompt: str | None = task["prompt"]
        callback_url: str | None = task["callback_url"]

        result: str | None = None
        error: str | None = None

        # Acquire the inhibitor before any work begins so the workstation
        # cannot sleep between now and when we release it at step 10.
        self._inhibitor.acquire(f"Ringmaster: running task {task_id}")

        try:
            # Register the task as in-flight so the scheduler can honour drain
            # requests correctly (drain waits for the current task to finish).
            self._scheduler.set_current(task_id)

            # Persist 'running' status before touching Ollama so that if the
            # process crashes mid-task the task is not silently stuck in 'queued'.
            db.update_task_started(self._conn, task_id)

            start = time.monotonic()

            try:
                result = await self._ollama.generate(model, prompt or "")
            except OllamaError as exc:
                # Treat Ollama errors as task-level failures, not worker crashes.
                # The dispatch loop continues; the task is marked failed.
                error = str(exc)

            duration = time.monotonic() - start

            # Persist the outcome.  update_task_completed determines whether to
            # use status='completed' or 'failed' based on whether error is set.
            db.update_task_completed(
                self._conn,
                task_id,
                result=result,
                error=error,
                duration=duration,
            )

        finally:
            # Always release the inhibitor — even if db.update_task_completed
            # raises or the Ollama call panics.  A leaked lock would prevent
            # the workstation from sleeping until the next reboot.
            self._inhibitor.release()

        # Notify the scheduler that the slot is free.  This must happen after
        # the inhibitor is released to ensure a pending drain pause does not
        # prevent the release from running (drain only blocks new tasks, not
        # cleanup of the finished one).
        self._scheduler.on_task_completed()

        # Build the webhook payload from the final task state.
        final_status = "failed" if error else "completed"
        payload = WebhookPayload(
            task_id=task_id,
            status=final_status,
            result=result,
            error=error,
            model=model,
            duration_seconds=duration,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

        # Fire the webhook after releasing the inhibitor so a slow callback
        # receiver does not extend the sleep-blocking window unnecessarily.
        await self._deliver_webhook(callback_url, payload)

        return True
