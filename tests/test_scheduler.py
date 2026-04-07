"""Tests for the Ringmaster Scheduler (queue state machine).

These tests verify the Scheduler's state transitions, priority ordering,
pause/resume/drain lifecycle, and queue depth enforcement.  All tests use
an in-memory SQLite database so they are fast and leave no filesystem state.

TDD: tests were written before the implementation to capture expected
behaviour precisely and prevent regressions as the scheduler evolves.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone

import pytest

from ringmaster import db
from ringmaster.config import QueueConfig
from ringmaster.scheduler import QueueFullError, Scheduler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_conn() -> sqlite3.Connection:
    """Open a fresh in-memory database, initialise the schema, and register a
    test client.

    Registering a client is required because the tasks table enforces a
    foreign-key constraint: tasks.client_id → clients.id.  Without a valid
    client row every insert_task call would fail with an integrity error.
    """
    conn = db.get_db(":memory:")
    db.init_db(conn)
    db.insert_client(conn, client_id="test-client", token_hash="testhash")
    return conn


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def make_scheduler(conn: sqlite3.Connection, **kwargs) -> Scheduler:
    """Build a Scheduler with a QueueConfig, overriding fields via *kwargs*.

    Passing kwargs allows individual tests to adjust e.g. max_queue_depth
    without having to construct the full QueueConfig themselves.
    """
    cfg = QueueConfig(**kwargs)
    return Scheduler(conn, cfg)


# ---------------------------------------------------------------------------
# submit_task
# ---------------------------------------------------------------------------


class TestSubmitTask:
    def test_submit_task_goes_to_queued(self):
        """A freshly submitted task must land in 'queued' status in the DB.

        This is the fundamental contract of submit_task: the task must be
        persisted and immediately visible to the queue polling logic.
        """
        conn = make_conn()
        scheduler = make_scheduler(conn)

        task_id = scheduler.submit_task(
            task_type="generate",
            model="llama3",
            prompt="hello",
            priority=3,
            client_id="test-client",
        )

        assert task_id, "submit_task should return a non-empty task ID"
        row = db.get_task(conn, task_id)
        assert row is not None, "task must exist in the database"
        assert row["status"] == "queued", "task must start life in 'queued' status"

    def test_submit_task_applies_default_priority(self):
        """When priority=None, submit_task must substitute config.default_priority.

        Callers should not be forced to specify a priority for every task; the
        config default covers the common case.
        """
        conn = make_conn()
        scheduler = make_scheduler(conn, default_priority=2)

        task_id = scheduler.submit_task(
            task_type="generate",
            model="llama3",
            prompt="hi",
            priority=None,
            client_id="test-client",
        )

        row = db.get_task(conn, task_id)
        assert row["priority"] == 2, "default_priority from config should be applied"

    def test_submit_task_returns_unique_ids(self):
        """Each submitted task must get a distinct ID.

        Duplicate IDs would cause silent data corruption when two tasks are
        submitted concurrently.
        """
        conn = make_conn()
        scheduler = make_scheduler(conn)

        ids = [
            scheduler.submit_task(
                task_type="generate",
                model="llama3",
                prompt="x",
                priority=3,
                client_id="test-client",
            )
            for _ in range(5)
        ]
        assert len(set(ids)) == 5, "every submitted task must receive a unique ID"


# ---------------------------------------------------------------------------
# Queue depth limit
# ---------------------------------------------------------------------------


class TestQueueDepthLimit:
    def test_queue_depth_limit(self):
        """Submitting a task beyond max_queue_depth must raise QueueFullError.

        The queue depth limit prevents a runaway client from consuming unbounded
        memory and ensures latency SLOs are met by capping backlog size.
        """
        conn = make_conn()
        scheduler = make_scheduler(conn, max_queue_depth=2)

        scheduler.submit_task(
            task_type="generate", model="llama3",
            prompt="1", priority=3, client_id="test-client",
        )
        scheduler.submit_task(
            task_type="generate", model="llama3",
            prompt="2", priority=3, client_id="test-client",
        )

        with pytest.raises(QueueFullError):
            scheduler.submit_task(
                task_type="generate", model="llama3",
                prompt="3", priority=3, client_id="test-client",
            )

    def test_queue_depth_counts_only_queued_tasks(self):
        """Tasks in non-queued states must not count against the depth limit.

        A running task has left the queue; counting it would incorrectly shrink
        the effective capacity while the worker is busy.
        """
        conn = make_conn()
        scheduler = make_scheduler(conn, max_queue_depth=1)

        task_id = scheduler.submit_task(
            task_type="generate", model="llama3",
            prompt="running", priority=3, client_id="test-client",
        )
        # Simulate the task moving to running state
        db.update_task_status(conn, task_id, "running")

        # Should succeed because the running task no longer counts towards depth
        scheduler.submit_task(
            task_type="generate", model="llama3",
            prompt="new", priority=3, client_id="test-client",
        )


# ---------------------------------------------------------------------------
# next_task
# ---------------------------------------------------------------------------


class TestNextTask:
    def test_next_task_returns_highest_priority(self):
        """next_task must return the task with the lowest priority number first.

        Lower priority numbers represent higher urgency (1 = highest, 5 = lowest).
        Getting this wrong would silently invert the urgency ordering for all
        submitted tasks.
        """
        conn = make_conn()
        scheduler = make_scheduler(conn)

        scheduler.submit_task(
            task_type="generate", model="llama3",
            prompt="low", priority=5, client_id="test-client",
        )
        high_id = scheduler.submit_task(
            task_type="generate", model="llama3",
            prompt="high", priority=1, client_id="test-client",
        )

        task = scheduler.next_task()
        assert task is not None
        assert task["id"] == high_id, "priority 1 must be returned before priority 5"

    def test_next_task_returns_none_when_empty(self):
        """next_task must return None when no queued tasks exist.

        The dispatch loop polls next_task in a tight loop; returning None is the
        signal to stop and wait for new submissions.
        """
        conn = make_conn()
        scheduler = make_scheduler(conn)
        assert scheduler.next_task() is None

    def test_next_task_returns_none_when_paused(self):
        """next_task must return None when the scheduler is paused, even if tasks
        are waiting.

        Pause is the mechanism used to stop task dispatch during drain and manual
        hold operations; ignoring pause state would defeat its purpose.
        """
        conn = make_conn()
        scheduler = make_scheduler(conn)

        scheduler.submit_task(
            task_type="generate", model="llama3",
            prompt="x", priority=3, client_id="test-client",
        )
        scheduler.pause()

        assert scheduler.next_task() is None, (
            "next_task must respect the paused flag and not dequeue tasks"
        )


# ---------------------------------------------------------------------------
# Pause and resume
# ---------------------------------------------------------------------------


class TestPauseAndResume:
    def test_pause_and_resume(self):
        """Pausing blocks dispatch; resuming re-enables it.

        This test exercises the full pause → no dispatch → resume → dispatch
        cycle to confirm that both state transitions are correctly recorded and
        respected.
        """
        conn = make_conn()
        scheduler = make_scheduler(conn)

        task_id = scheduler.submit_task(
            task_type="generate", model="llama3",
            prompt="test", priority=3, client_id="test-client",
        )

        scheduler.pause()
        assert scheduler.is_paused is True
        assert scheduler.next_task() is None, "no tasks should dispatch while paused"

        scheduler.resume()
        assert scheduler.is_paused is False

        task = scheduler.next_task()
        assert task is not None
        assert task["id"] == task_id, "task should be available again after resume"

    def test_resume_clears_draining_flag(self):
        """resume() must clear the draining flag so subsequent drains work correctly.

        If drain state persisted across resume calls, a second drain would
        immediately pause without waiting for a task to finish.
        """
        conn = make_conn()
        scheduler = make_scheduler(conn)

        # Drain with no current task → pauses immediately
        scheduler.drain()
        assert scheduler.is_paused is True

        scheduler.resume()
        assert scheduler.is_draining is False


# ---------------------------------------------------------------------------
# Drain
# ---------------------------------------------------------------------------


class TestDrain:
    def test_drain_pauses_immediately_with_no_current_task(self):
        """drain() must pause immediately when no task is currently running.

        When the queue is idle there is nothing to wait for, so drain completes
        instantly by switching to paused.
        """
        conn = make_conn()
        scheduler = make_scheduler(conn)

        scheduler.drain()
        assert scheduler.is_paused is True, (
            "drain with no running task must pause immediately"
        )

    def test_drain_pauses_after_current_task_completes(self):
        """drain() with a running task must wait until that task finishes.

        The scheduler must not accept new work after drain; but it must let the
        in-flight task run to completion rather than interrupting it.
        """
        conn = make_conn()
        scheduler = make_scheduler(conn)

        task_id = scheduler.submit_task(
            task_type="generate", model="llama3",
            prompt="work", priority=3, client_id="test-client",
        )
        scheduler.set_current(task_id)
        db.update_task_status(conn, task_id, "running")

        scheduler.drain()
        assert scheduler.is_paused is False, (
            "drain must NOT pause while a task is still running"
        )
        assert scheduler.is_draining is True, (
            "drain must set the draining flag to pause after current task finishes"
        )

        scheduler.on_task_completed()
        assert scheduler.is_paused is True, (
            "on_task_completed must trigger the pending drain pause"
        )


# ---------------------------------------------------------------------------
# cancel_current
# ---------------------------------------------------------------------------


class TestCancelCurrent:
    def test_cancel_current_task(self):
        """cancel_current must mark the running task as 'interrupted' and clear
        the current task slot.

        'interrupted' is distinct from 'cancelled' (user-requested cancellation
        before dispatch) and 'failed' (worker-side error) — it signals that the
        task was actively stopped mid-execution.
        """
        conn = make_conn()
        scheduler = make_scheduler(conn)

        task_id = scheduler.submit_task(
            task_type="generate", model="llama3",
            prompt="work", priority=3, client_id="test-client",
        )
        scheduler.set_current(task_id)
        db.update_task_status(conn, task_id, "running")

        cancelled_id = scheduler.cancel_current()

        assert cancelled_id == task_id, (
            "cancel_current must return the ID of the task it cancelled"
        )
        assert scheduler.current_task_id is None, (
            "cancel_current must clear the current task slot"
        )
        row = db.get_task(conn, task_id)
        assert row["status"] == "interrupted", (
            "the cancelled task must be marked 'interrupted' in the DB"
        )

    def test_cancel_current_returns_none_when_no_task(self):
        """cancel_current returns None when no task is currently running.

        Calling cancel with an idle scheduler is a no-op; returning None lets
        the caller distinguish between a successful cancellation and a no-op.
        """
        conn = make_conn()
        scheduler = make_scheduler(conn)

        result = scheduler.cancel_current()
        assert result is None


# ---------------------------------------------------------------------------
# queue_depth
# ---------------------------------------------------------------------------


class TestQueueDepth:
    def test_queue_depth_reflects_queued_tasks(self):
        """queue_depth must count exactly the tasks in 'queued' status."""
        conn = make_conn()
        scheduler = make_scheduler(conn)

        assert scheduler.queue_depth() == 0

        scheduler.submit_task(
            task_type="generate", model="llama3",
            prompt="a", priority=3, client_id="test-client",
        )
        assert scheduler.queue_depth() == 1

        scheduler.submit_task(
            task_type="generate", model="llama3",
            prompt="b", priority=3, client_id="test-client",
        )
        assert scheduler.queue_depth() == 2


# ---------------------------------------------------------------------------
# defer_task / approve_task
# ---------------------------------------------------------------------------


class TestDeferAndApprove:
    def test_defer_task(self):
        """defer_task must move a queued task to 'deferred' status.

        Deferred tasks are excluded from normal dispatch and must be explicitly
        re-approved before they will run.
        """
        conn = make_conn()
        scheduler = make_scheduler(conn)

        task_id = scheduler.submit_task(
            task_type="generate", model="llama3",
            prompt="x", priority=3, client_id="test-client",
        )
        scheduler.defer_task(task_id)

        row = db.get_task(conn, task_id)
        assert row["status"] == "deferred"

    def test_approve_task(self):
        """approve_task must move a deferred task back to 'queued' status.

        Approval is the return path from deferred: the task re-enters the normal
        dispatch queue as if it had just been submitted.
        """
        conn = make_conn()
        scheduler = make_scheduler(conn)

        task_id = scheduler.submit_task(
            task_type="generate", model="llama3",
            prompt="x", priority=3, client_id="test-client",
        )
        scheduler.defer_task(task_id)
        scheduler.approve_task(task_id)

        row = db.get_task(conn, task_id)
        assert row["status"] == "queued"

    def test_deferred_task_not_returned_by_next_task(self):
        """next_task must not return deferred tasks.

        Deferred tasks are in limbo awaiting approval; dispatching them before
        they are approved would violate the unattended_policy contract.
        """
        conn = make_conn()
        scheduler = make_scheduler(conn)

        task_id = scheduler.submit_task(
            task_type="generate", model="llama3",
            prompt="x", priority=3, client_id="test-client",
        )
        scheduler.defer_task(task_id)

        assert scheduler.next_task() is None, (
            "deferred task must not be returned by next_task"
        )


# ---------------------------------------------------------------------------
# set_current / on_task_completed
# ---------------------------------------------------------------------------


class TestCurrentTask:
    def test_set_current_tracks_task_id(self):
        """set_current must update the current_task_id property."""
        conn = make_conn()
        scheduler = make_scheduler(conn)

        task_id = scheduler.submit_task(
            task_type="generate", model="llama3",
            prompt="x", priority=3, client_id="test-client",
        )
        scheduler.set_current(task_id)
        assert scheduler.current_task_id == task_id

    def test_on_task_completed_clears_current(self):
        """on_task_completed must clear the current task slot."""
        conn = make_conn()
        scheduler = make_scheduler(conn)

        task_id = scheduler.submit_task(
            task_type="generate", model="llama3",
            prompt="x", priority=3, client_id="test-client",
        )
        scheduler.set_current(task_id)
        scheduler.on_task_completed()

        assert scheduler.current_task_id is None

    def test_on_task_completed_does_not_pause_without_drain(self):
        """on_task_completed must not pause when draining is not active.

        Only a drain request should cause a pause on completion; a normal task
        finish should leave the scheduler running so the next task can proceed.
        """
        conn = make_conn()
        scheduler = make_scheduler(conn)

        task_id = scheduler.submit_task(
            task_type="generate", model="llama3",
            prompt="x", priority=3, client_id="test-client",
        )
        scheduler.set_current(task_id)
        scheduler.on_task_completed()

        assert scheduler.is_paused is False


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_submits_produce_unique_ids(self):
        """Submitting tasks from multiple threads must never produce duplicate IDs
        or corrupt scheduler state."""
        conn = make_conn()
        scheduler = make_scheduler(conn, max_queue_depth=200)
        results = []
        errors = []

        def submit_one(i: int) -> None:
            try:
                task_id = scheduler.submit_task(
                    task_type="generate",
                    model="llama3",
                    prompt=f"thread-{i}",
                    priority=3,
                    client_id="test-client",
                )
                results.append(task_id)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=submit_one, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Unexpected errors: {errors}"
        assert len(results) == 20
        assert len(set(results)) == 20, "All task IDs must be unique"

    def test_concurrent_pause_and_submit(self):
        """Pausing while tasks are being submitted must not crash or produce
        inconsistent state."""
        conn = make_conn()
        scheduler = make_scheduler(conn, max_queue_depth=200)
        errors = []

        def submit_many() -> None:
            for i in range(10):
                try:
                    scheduler.submit_task(
                        task_type="generate",
                        model="llama3",
                        prompt=f"s-{i}",
                        priority=3,
                        client_id="test-client",
                    )
                except Exception as exc:
                    errors.append(exc)

        def toggle_pause() -> None:
            for _ in range(10):
                scheduler.pause()
                scheduler.resume()

        t1 = threading.Thread(target=submit_many)
        t2 = threading.Thread(target=toggle_pause)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Unexpected errors: {errors}"
        assert isinstance(scheduler.is_paused, bool)

    def test_concurrent_cancel_and_complete(self):
        """cancel_current and on_task_completed racing must not corrupt state."""
        conn = make_conn()
        scheduler = make_scheduler(conn)

        task_id = scheduler.submit_task(
            task_type="generate",
            model="llama3",
            prompt="race",
            priority=3,
            client_id="test-client",
        )
        scheduler.set_current(task_id)
        db.update_task_status(conn, task_id, "running")

        errors = []

        def do_cancel() -> None:
            try:
                scheduler.cancel_current()
            except Exception as exc:
                errors.append(exc)

        def do_complete() -> None:
            try:
                scheduler.on_task_completed()
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=do_cancel)
        t2 = threading.Thread(target=do_complete)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Unexpected errors: {errors}"
        assert scheduler.current_task_id is None
