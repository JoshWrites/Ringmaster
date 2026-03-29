"""Tests for the Ringmaster CLI commands.

The CLI is a thin HTTP client over the REST API.  These tests use Click's
CliRunner to invoke commands in-process and pytest-httpx to intercept the
outbound httpx calls, so no real server is required.

Design notes:
  - Each test mocks exactly the endpoint the command calls and nothing more.
  - Assertions focus on output content, not formatting details, so they remain
    stable if the display is tweaked.
  - The RINGMASTER_TOKEN env var is provided via the env= argument to avoid
    leaking into the test process environment.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner
from pytest_httpx import HTTPXMock

from ringmaster.cli.main import cli


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

BASE_URL = "http://localhost:8420"
TOKEN = "test-token"
_ENV = {"RINGMASTER_TOKEN": TOKEN}


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_cli_status(httpx_mock: HTTPXMock) -> None:
    """'ringmaster status' should display the state field from GET /status."""
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE_URL}/status",
        json={
            "state": "idle",
            "queue_depth": 0,
            "current_task": None,
            "user_present": False,
            "queue_paused": False,
        },
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["status"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert "idle" in result.output


# ---------------------------------------------------------------------------
# queue
# ---------------------------------------------------------------------------


def test_cli_queue(httpx_mock: HTTPXMock) -> None:
    """'ringmaster queue' should display task IDs from GET /tasks."""
    task_id = "abc-123"
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE_URL}/tasks",
        json=[
            {
                "id": task_id,
                "task_type": "generate",
                "model": "llama3:8b",
                "priority": 3,
                "status": "queued",
                "client_id": "test-client",
                "submitted_at": "2024-01-01T00:00:00Z",
                "started_at": None,
                "completed_at": None,
                "deadline": None,
                "prompt": "Hello",
                "result": None,
                "error": None,
                "gpu_used": None,
                "duration_seconds": None,
                "callback_url": None,
                "unattended_policy": "run",
                "metadata": {},
            }
        ],
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["queue"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert task_id in result.output


# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------


def test_cli_submit(httpx_mock: HTTPXMock) -> None:
    """'ringmaster submit' should display the new task ID from POST /tasks."""
    task_id = "new-task-456"
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE_URL}/tasks",
        json={
            "id": task_id,
            "task_type": "generate",
            "model": "llama3:8b",
            "priority": 3,
            "status": "queued",
            "client_id": "cli",
            "submitted_at": "2024-01-01T00:00:00Z",
            "started_at": None,
            "completed_at": None,
            "deadline": None,
            "prompt": "Say hello",
            "result": None,
            "error": None,
            "gpu_used": None,
            "duration_seconds": None,
            "callback_url": None,
            "unattended_policy": "run",
            "metadata": {},
        },
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "submit",
            "--model", "llama3:8b",
            "--prompt", "Say hello",
            "--client-id", "cli",
        ],
        env=_ENV,
    )

    assert result.exit_code == 0, result.output
    assert task_id in result.output


# ---------------------------------------------------------------------------
# pause
# ---------------------------------------------------------------------------


def test_cli_pause(httpx_mock: HTTPXMock) -> None:
    """'ringmaster pause' should confirm the queue is paused."""
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE_URL}/queue/pause",
        json={"queue_paused": True},
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["pause"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert "paused" in result.output.lower()


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


def test_cli_resume(httpx_mock: HTTPXMock) -> None:
    """'ringmaster resume' should confirm the queue has resumed."""
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE_URL}/queue/resume",
        json={"queue_paused": False},
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["resume"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert "resumed" in result.output.lower()
