"""Tests for the Ringmaster Worker (background task execution engine).

The Worker ties together the Scheduler, OllamaClient, SleepInhibitor, and
webhook delivery into a single run_one() call that processes one task from the
queue.  These tests verify the full lifecycle: success path, error path,
webhook firing, and early exit when the scheduler is paused.

All tests use an in-memory SQLite database and mock the external collaborators
(Ollama, inhibitor, webhook) so no real GPU or network I/O occurs.

TDD: tests were written before the implementation to capture the expected
behaviour precisely and to prevent regressions as the worker evolves.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from ringmaster import db
from ringmaster.config import QueueConfig
from ringmaster.models import WebhookPayload
from ringmaster.ollama import OllamaError
from ringmaster.scheduler import Scheduler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_conn() -> sqlite3.Connection:
    """Open a fresh in-memory database and initialise the schema.

    Also registers a test client row because the tasks table enforces a
    foreign-key constraint: tasks.client_id → clients.id.  Without it every
    insert_task call would fail with an IntegrityError.
    """
    conn = db.get_db(":memory:")
    db.init_db(conn)
    db.insert_client(conn, client_id="test-client", token_hash="testhash")
    return conn


def make_scheduler(conn: sqlite3.Connection) -> Scheduler:
    """Build a Scheduler with default QueueConfig using the given connection."""
    return Scheduler(conn, QueueConfig())


def submit_test_task(
    scheduler: Scheduler,
    *,
    model: str = "llama3",
    prompt: str = "Say hello.",
    callback_url: str | None = None,
) -> str:
    """Submit a minimal 'generate' task and return its ID.

    Centralises task creation so individual tests only need to express the
    parameters relevant to the scenario under test.
    """
    return scheduler.submit_task(
        task_type="generate",
        model=model,
        prompt=prompt,
        priority=3,
        client_id="test-client",
        callback_url=callback_url,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWorkerRunsDiscreteTask:
    """run_one() executes the task end-to-end on the happy path."""

    @pytest.mark.asyncio
    async def test_worker_runs_discrete_task(self):
        """A queued task must be executed, stored as completed, and return True.

        Verifies the full happy-path lifecycle:
          - run_one() returns True (a task was found and dispatched).
          - The task status is 'completed' in the database.
          - The result text from Ollama is persisted.
          - duration_seconds is populated (task took some measurable time).
          - ollama.generate() was called exactly once with the right arguments.
          - The sleep inhibitor was acquired before calling Ollama and released
            afterwards.
        """
        from ringmaster.worker import Worker

        conn = make_conn()
        scheduler = make_scheduler(conn)
        task_id = submit_test_task(scheduler, model="llama3", prompt="Say hello.")

        mock_ollama = MagicMock()
        mock_ollama.generate = AsyncMock(return_value="Hello, world!")

        mock_inhibitor = MagicMock()
        mock_deliver = AsyncMock(return_value=True)

        worker = Worker(conn, scheduler, mock_ollama, mock_inhibitor, mock_deliver)
        result = await worker.run_one()

        assert result is True, "run_one() must return True when a task is dispatched"

        row = db.get_task(conn, task_id)
        assert row is not None
        assert row["status"] == "completed", (
            "task must be marked 'completed' after successful Ollama response"
        )
        assert row["result"] == "Hello, world!", (
            "the Ollama response text must be persisted as the task result"
        )
        assert row["duration_seconds"] is not None, (
            "wall-clock duration must be recorded for completed tasks"
        )
        assert row["duration_seconds"] >= 0, "duration must be non-negative"

        mock_ollama.generate.assert_called_once_with("llama3", "Say hello.")

        mock_inhibitor.acquire.assert_called_once()
        mock_inhibitor.release.assert_called_once()


class TestWorkerHandlesOllamaError:
    """run_one() records a failure and releases the inhibitor when Ollama errors."""

    @pytest.mark.asyncio
    async def test_worker_handles_ollama_error(self):
        """An OllamaError must set the task to 'failed' and not leak the inhibitor.

        When Ollama returns an error, the worker must:
          - Mark the task 'failed' (not 'completed').
          - Persist the error message so the caller can diagnose the failure.
          - Release the sleep inhibitor even though execution failed — leaving
            it held would prevent the workstation from sleeping indefinitely.
          - Still return True because a task *was* dequeued and processed (the
            caller decides whether to retry based on task status, not run_one's
            return value).
        """
        from ringmaster.worker import Worker

        conn = make_conn()
        scheduler = make_scheduler(conn)
        task_id = submit_test_task(scheduler)

        mock_ollama = MagicMock()
        mock_ollama.generate = AsyncMock(
            side_effect=OllamaError("Ollama returned 500: model not found")
        )

        mock_inhibitor = MagicMock()
        mock_deliver = AsyncMock(return_value=True)

        worker = Worker(conn, scheduler, mock_ollama, mock_inhibitor, mock_deliver)
        result = await worker.run_one()

        assert result is True, (
            "run_one() must still return True even when the task fails — "
            "a task was dequeued and an outcome was recorded"
        )

        row = db.get_task(conn, task_id)
        assert row is not None
        assert row["status"] == "failed", (
            "task must be marked 'failed' when Ollama raises OllamaError"
        )
        assert row["error"] is not None, "error message must be persisted on failure"
        assert "500" in row["error"] or "model not found" in row["error"], (
            "the persisted error must contain information from the original exception"
        )

        # The inhibitor must always be released to avoid permanent sleep blocking.
        mock_inhibitor.release.assert_called_once()


class TestWorkerFiresWebhook:
    """run_one() notifies the callback URL after task completion."""

    @pytest.mark.asyncio
    async def test_worker_fires_webhook(self):
        """deliver_webhook must be called with a correctly-populated WebhookPayload.

        The payload must reflect the actual task outcome so the receiving system
        can make decisions (e.g. trigger follow-up work) based on accurate data.
        Specifically:
          - task_id matches the submitted task.
          - status is 'completed' for a successful generation.
          - result contains the Ollama response text.
          - model matches the model the task was submitted with.
        """
        from ringmaster.worker import Worker

        conn = make_conn()
        scheduler = make_scheduler(conn)
        task_id = submit_test_task(
            scheduler,
            model="mistral",
            prompt="Summarise this.",
            callback_url="https://example.com/cb",
        )

        mock_ollama = MagicMock()
        mock_ollama.generate = AsyncMock(return_value="A short summary.")

        mock_inhibitor = MagicMock()
        mock_deliver = AsyncMock(return_value=True)

        worker = Worker(conn, scheduler, mock_ollama, mock_inhibitor, mock_deliver)
        await worker.run_one()

        mock_deliver.assert_called_once()
        delivered_url, delivered_payload = mock_deliver.call_args.args

        assert delivered_url == "https://example.com/cb", (
            "webhook must be sent to the task's callback_url"
        )
        assert isinstance(delivered_payload, WebhookPayload), (
            "webhook must be called with a WebhookPayload instance"
        )
        assert delivered_payload.task_id == task_id
        assert delivered_payload.status == "completed"
        assert delivered_payload.result == "A short summary."
        assert delivered_payload.model == "mistral"

    @pytest.mark.asyncio
    async def test_worker_fires_webhook_on_failure(self):
        """deliver_webhook must also be called when the task fails.

        The callback receiver should be able to act on failures (e.g. alert or
        retry), so the webhook must fire regardless of task outcome.
        """
        from ringmaster.worker import Worker

        conn = make_conn()
        scheduler = make_scheduler(conn)
        task_id = submit_test_task(
            scheduler,
            callback_url="https://example.com/cb",
        )

        mock_ollama = MagicMock()
        mock_ollama.generate = AsyncMock(
            side_effect=OllamaError("GPU out of memory")
        )

        mock_inhibitor = MagicMock()
        mock_deliver = AsyncMock(return_value=True)

        worker = Worker(conn, scheduler, mock_ollama, mock_inhibitor, mock_deliver)
        await worker.run_one()

        mock_deliver.assert_called_once()
        delivered_url, delivered_payload = mock_deliver.call_args.args

        assert delivered_url == "https://example.com/cb"
        assert delivered_payload.task_id == task_id
        assert delivered_payload.status == "failed"
        assert delivered_payload.result is None
        assert "GPU out of memory" in delivered_payload.error


class TestWorkerSkipsWhenPaused:
    """run_one() returns False immediately when the scheduler has no task to offer."""

    @pytest.mark.asyncio
    async def test_worker_skips_when_paused(self):
        """run_one() must return False and not call Ollama when the scheduler is paused.

        The scheduler returns None from next_task() when paused; the worker must
        treat this as 'nothing to do' rather than as an error.  Calling Ollama
        when there is no task would be a bug (no model or prompt to pass).
        """
        from ringmaster.worker import Worker

        conn = make_conn()
        scheduler = make_scheduler(conn)

        # Submit a task, then pause — the task exists but must not be dispatched.
        submit_test_task(scheduler)
        scheduler.pause()

        mock_ollama = MagicMock()
        mock_ollama.generate = AsyncMock()

        mock_inhibitor = MagicMock()
        mock_deliver = AsyncMock()

        worker = Worker(conn, scheduler, mock_ollama, mock_inhibitor, mock_deliver)
        result = await worker.run_one()

        assert result is False, (
            "run_one() must return False when the scheduler returns no task"
        )
        mock_ollama.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_worker_returns_false_on_empty_queue(self):
        """run_one() must return False when the queue is empty.

        An empty queue is the normal idle state; returning False signals to the
        dispatch loop that it should wait before polling again.
        """
        from ringmaster.worker import Worker

        conn = make_conn()
        scheduler = make_scheduler(conn)

        mock_ollama = MagicMock()
        mock_ollama.generate = AsyncMock()
        mock_inhibitor = MagicMock()
        mock_deliver = AsyncMock()

        worker = Worker(conn, scheduler, mock_ollama, mock_inhibitor, mock_deliver)
        result = await worker.run_one()

        assert result is False
        mock_ollama.generate.assert_not_called()
