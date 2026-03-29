"""Tests for the Ringmaster SQLite database layer.

We use in-memory SQLite databases (":memory:") so each test gets a clean
slate without touching the filesystem.  The tests deliberately cover the
full contract of each public db.py function so that regressions in SQL or
Python logic are caught before they reach the scheduler or worker layers.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone


from ringmaster import db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


DEFAULT_TEST_CLIENTS = ["c1", "client-1", "client-2", "alpha", "beta"]


def make_connection(extra_clients: list[str] | None = None) -> sqlite3.Connection:
    """Open a fresh in-memory SQLite connection, initialise the schema, and
    pre-register the standard set of test client IDs.

    Most task and session operations require a valid client_id due to the
    foreign-key constraint on the clients table.  Pre-registering a fixed set
    of well-known test IDs avoids boilerplate insert_client calls in every
    test body.  Pass *extra_clients* to register additional IDs beyond the
    defaults.

    Args:
        extra_clients: Optional list of additional client IDs to register.

    Returns:
        An initialised in-memory SQLite connection.
    """
    conn = db.get_db(":memory:")
    db.init_db(conn)
    for cid in DEFAULT_TEST_CLIENTS + (extra_clients or []):
        db.insert_client(conn, client_id=cid, token_hash="testhash")
    return conn


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def utc_in(seconds: int) -> str:
    """Return a future UTC timestamp as ISO 8601, offset by *seconds*."""
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestInitDb:
    def test_init_db_creates_tables(self):
        """init_db must create the tasks, sessions, clients, and power_events tables."""
        conn = make_connection()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        assert "tasks" in tables
        assert "sessions" in tables
        assert "clients" in tables
        assert "power_events" in tables

    def test_init_db_creates_indexes(self):
        """init_db should create at least one index to support queue ordering."""
        conn = make_connection()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
        indexes = {row[0] for row in cursor.fetchall()}
        # At minimum there must be indexes on the tasks table to support
        # efficient queue polling
        assert len(indexes) > 0


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


class TestInsertAndFetchTask:
    def test_insert_and_fetch_task(self):
        """A task inserted with insert_task should be retrievable via get_task."""
        conn = make_connection()
        submitted_at = utc_now()
        task_id = db.insert_task(
            conn,
            task_type="generate",
            model="llama3",
            client_id="client-1",
            submitted_at=submitted_at,
        )
        assert task_id  # must return a non-empty string ID

        row = db.get_task(conn, task_id)
        assert row is not None
        assert row["id"] == task_id
        assert row["task_type"] == "generate"
        assert row["model"] == "llama3"
        assert row["client_id"] == "client-1"
        assert row["status"] == "queued"  # default initial status
        assert row["submitted_at"] == submitted_at

    def test_insert_task_stores_optional_fields(self):
        """Optional fields passed to insert_task should be persisted."""
        conn = make_connection()
        deadline = utc_in(3600)
        task_id = db.insert_task(
            conn,
            task_type="embed",
            model="nomic-embed-text",
            client_id="client-2",
            submitted_at=utc_now(),
            priority=1,
            deadline=deadline,
            prompt="embed this",
            callback_url="http://example.com/cb",
            unattended_policy="defer",
            metadata={"source": "ci"},
        )
        row = db.get_task(conn, task_id)
        assert row["priority"] == 1
        assert row["deadline"] == deadline
        assert row["prompt"] == "embed this"
        assert row["callback_url"] == "http://example.com/cb"
        assert row["unattended_policy"] == "defer"

    def test_get_task_returns_none_for_unknown_id(self):
        """get_task must return None when the ID does not exist."""
        conn = make_connection()
        assert db.get_task(conn, "nonexistent-id") is None


class TestUpdateTaskStatus:
    def test_update_task_status(self):
        """update_task_status should change the status field of the task."""
        conn = make_connection()
        task_id = db.insert_task(
            conn, task_type="generate", model="llama3",
            client_id="c1", submitted_at=utc_now(),
        )
        db.update_task_status(conn, task_id, "cancelled")
        row = db.get_task(conn, task_id)
        assert row["status"] == "cancelled"

    def test_update_task_started(self):
        """update_task_started should set status to 'running' and record started_at."""
        conn = make_connection()
        task_id = db.insert_task(
            conn, task_type="generate", model="llama3",
            client_id="c1", submitted_at=utc_now(),
        )
        db.update_task_started(conn, task_id)
        row = db.get_task(conn, task_id)
        assert row["status"] == "running"
        assert row["started_at"] is not None

    def test_update_task_completed_success(self):
        """update_task_completed should set status, result, gpu_used, and duration."""
        conn = make_connection()
        task_id = db.insert_task(
            conn, task_type="generate", model="llama3",
            client_id="c1", submitted_at=utc_now(),
        )
        db.update_task_started(conn, task_id)
        db.update_task_completed(
            conn, task_id,
            result="Hello!", gpu_used="rtx4090", duration=1.5,
        )
        row = db.get_task(conn, task_id)
        assert row["status"] == "completed"
        assert row["result"] == "Hello!"
        assert row["gpu_used"] == "rtx4090"
        assert row["duration_seconds"] == 1.5
        assert row["completed_at"] is not None

    def test_update_task_completed_failure(self):
        """update_task_completed with an error should set status to 'failed'."""
        conn = make_connection()
        task_id = db.insert_task(
            conn, task_type="generate", model="llama3",
            client_id="c1", submitted_at=utc_now(),
        )
        db.update_task_started(conn, task_id)
        db.update_task_completed(conn, task_id, error="OOM")
        row = db.get_task(conn, task_id)
        assert row["status"] == "failed"
        assert row["error"] == "OOM"


class TestListTasks:
    def test_list_tasks_by_status(self):
        """list_tasks(status=...) should return only tasks with that status."""
        conn = make_connection()
        t1 = db.insert_task(
            conn, task_type="generate", model="llama3",
            client_id="c1", submitted_at=utc_now(),
        )
        t2 = db.insert_task(
            conn, task_type="generate", model="llama3",
            client_id="c1", submitted_at=utc_now(),
        )
        db.update_task_status(conn, t1, "cancelled")

        queued = db.list_tasks(conn, status="queued")
        queued_ids = {r["id"] for r in queued}
        assert t2 in queued_ids
        assert t1 not in queued_ids

    def test_list_tasks_by_client_id(self):
        """list_tasks(client_id=...) should filter to a specific client."""
        conn = make_connection()
        db.insert_task(
            conn, task_type="generate", model="llama3",
            client_id="alpha", submitted_at=utc_now(),
        )
        db.insert_task(
            conn, task_type="generate", model="llama3",
            client_id="beta", submitted_at=utc_now(),
        )
        results = db.list_tasks(conn, client_id="alpha")
        assert all(r["client_id"] == "alpha" for r in results)
        assert len(results) == 1

    def test_list_tasks_limit(self):
        """list_tasks respects the limit parameter."""
        conn = make_connection()
        for _ in range(5):
            db.insert_task(
                conn, task_type="generate", model="llama3",
                client_id="c1", submitted_at=utc_now(),
            )
        results = db.list_tasks(conn, limit=3)
        assert len(results) <= 3

    def test_list_tasks_no_filter_returns_all(self):
        """list_tasks with no filters returns all tasks (up to limit)."""
        conn = make_connection()
        for _ in range(3):
            db.insert_task(
                conn, task_type="generate", model="llama3",
                client_id="c1", submitted_at=utc_now(),
            )
        results = db.list_tasks(conn)
        assert len(results) == 3


# ---------------------------------------------------------------------------
# Queue ordering
# ---------------------------------------------------------------------------


class TestQueueOrdering:
    def test_queue_ordering_priority_first(self):
        """get_next_queued_task must return the task with the lowest priority number."""
        conn = make_connection()
        now = utc_now()
        db.insert_task(
            conn, task_type="generate", model="llama3",
            client_id="c1", submitted_at=now, priority=5,
        )
        high_prio_id = db.insert_task(
            conn, task_type="generate", model="llama3",
            client_id="c1", submitted_at=now, priority=1,
        )
        next_task = db.get_next_queued_task(conn)
        assert next_task is not None
        assert next_task["id"] == high_prio_id, (
            "Priority 1 task should be dequeued before priority 5"
        )

    def test_queue_ordering_deadline_before_null(self):
        """Tasks with a deadline should be dequeued before tasks without one."""
        conn = make_connection()
        now = utc_now()
        # Insert the no-deadline task first to rule out FIFO as the reason
        db.insert_task(
            conn, task_type="generate", model="llama3",
            client_id="c1", submitted_at=now, priority=3,
        )
        deadline_id = db.insert_task(
            conn, task_type="generate", model="llama3",
            client_id="c1", submitted_at=now,
            priority=3, deadline=utc_in(3600),
        )
        next_task = db.get_next_queued_task(conn)
        assert next_task is not None
        assert next_task["id"] == deadline_id, (
            "Task with a deadline should be dequeued before task without deadline"
        )

    def test_queue_ordering_submitted_at_tiebreak(self):
        """When priority and deadline are equal, earlier submitted_at wins."""
        conn = make_connection()
        earlier = utc_now()
        t1 = db.insert_task(
            conn, task_type="generate", model="llama3",
            client_id="c1", submitted_at=earlier, priority=3,
        )
        # Add a tiny delay so the timestamps differ
        time.sleep(0.01)
        later = utc_now()
        db.insert_task(
            conn, task_type="generate", model="llama3",
            client_id="c1", submitted_at=later, priority=3,
        )
        next_task = db.get_next_queued_task(conn)
        assert next_task is not None
        assert next_task["id"] == t1, (
            "Earlier submitted_at should win the tiebreak"
        )

    def test_get_next_queued_task_returns_none_when_empty(self):
        """get_next_queued_task returns None when there are no queued tasks."""
        conn = make_connection()
        assert db.get_next_queued_task(conn) is None

    def test_get_next_queued_task_skips_non_queued(self):
        """get_next_queued_task ignores tasks that are running or completed."""
        conn = make_connection()
        task_id = db.insert_task(
            conn, task_type="generate", model="llama3",
            client_id="c1", submitted_at=utc_now(),
        )
        db.update_task_status(conn, task_id, "running")
        assert db.get_next_queued_task(conn) is None


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class TestSessions:
    def test_insert_and_fetch_session(self):
        """A session inserted via insert_session should be retrievable by ID."""
        conn = make_connection()
        opened_at = utc_now()
        sess_id = db.insert_session(
            conn,
            client_id="client-1",
            model="llama3",
            opened_at=opened_at,
            idle_timeout_seconds=600,
        )
        assert sess_id  # must return a non-empty string

        row = db.get_session(conn, sess_id)
        assert row is not None
        assert row["id"] == sess_id
        assert row["client_id"] == "client-1"
        assert row["model"] == "llama3"
        assert row["status"] == "open"
        assert row["opened_at"] == opened_at
        assert row["idle_timeout_seconds"] == 600
        assert row["gpu_label"] is None

    def test_update_session_activity(self):
        """update_session_activity should update last_activity_at."""
        conn = make_connection()
        sess_id = db.insert_session(
            conn, client_id="c1", model="llama3",
            opened_at=utc_now(), idle_timeout_seconds=600,
        )
        db.update_session_activity(conn, sess_id)
        row = db.get_session(conn, sess_id)
        assert row["last_activity_at"] is not None

    def test_close_session(self):
        """close_session should set the session status to 'closed'."""
        conn = make_connection()
        sess_id = db.insert_session(
            conn, client_id="c1", model="llama3",
            opened_at=utc_now(), idle_timeout_seconds=600,
        )
        db.close_session(conn, sess_id)
        row = db.get_session(conn, sess_id)
        assert row["status"] == "closed"

    def test_get_session_returns_none_for_unknown_id(self):
        """get_session must return None when the session ID does not exist."""
        conn = make_connection()
        assert db.get_session(conn, "nonexistent") is None


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


class TestClients:
    def test_insert_and_fetch_client(self):
        """insert_client and get_client_by_id form a round-trip."""
        conn = make_connection()
        db.insert_client(conn, client_id="client-x", token_hash="hash123")
        row = db.get_client_by_id(conn, "client-x")
        assert row is not None
        assert row["id"] == "client-x"
        assert row["token_hash"] == "hash123"

    def test_get_client_returns_none_for_unknown_id(self):
        """get_client_by_id returns None for an unknown client ID."""
        conn = make_connection()
        assert db.get_client_by_id(conn, "nobody") is None


# ---------------------------------------------------------------------------
# Power events
# ---------------------------------------------------------------------------


class TestPowerEvents:
    def test_log_power_event_minimal(self):
        """log_power_event should succeed with only event_type provided."""
        conn = make_connection()
        # Should not raise
        db.log_power_event(conn, event_type="sleep")

    def test_log_power_event_full(self):
        """log_power_event should accept all optional fields."""
        conn = make_connection()
        db.log_power_event(
            conn,
            event_type="wake",
            source="scheduler",
            detail="task task-1 required GPU",
        )
        # Verify the row was actually written
        cursor = conn.execute("SELECT * FROM power_events ORDER BY recorded_at DESC LIMIT 1")
        row = cursor.fetchone()
        assert row is not None
