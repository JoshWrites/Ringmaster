"""Tests for POST/GET /tasks route handlers.

These tests exercise the full HTTP stack using an in-process ASGI client,
verifying that task submission, retrieval, listing, and auth enforcement all
work correctly against a real (temp-file) SQLite database.
"""

from __future__ import annotations

from pathlib import Path

import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from ringmaster.server.app import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncClient:
    """Create a fully-wired FastAPI app with a registered test client.

    Uses a temp SQLite database so tests are hermetically isolated from each
    other and from the developer's live state.
    """
    config_path = tmp_path / "ringmaster.yaml"
    config_path.write_text("")  # All defaults are sufficient for route tests.

    app, auth_manager = await create_app(config_path, db_path=tmp_path / "test.db")

    # Register a test client so we have a valid bearer token to send.
    raw_token = auth_manager.register("test-client")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {raw_token}"},
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_submit_task(client: AsyncClient) -> None:
    """POST /tasks should create a task and return 201 with id and queued status."""
    resp = await client.post(
        "/tasks",
        json={
            "task_type": "generate",
            "model": "llama3:8b",
            "client_id": "test-client",
            "prompt": "Hello world",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "id" in body
    assert body["status"] == "queued"


async def test_get_task(client: AsyncClient) -> None:
    """Submitting a task then GET /tasks/{id} should return 200 with matching data."""
    submit = await client.post(
        "/tasks",
        json={"task_type": "generate", "model": "llama3:8b", "client_id": "test-client"},
    )
    task_id = submit.json()["id"]

    resp = await client.get(f"/tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == task_id


async def test_list_tasks(client: AsyncClient) -> None:
    """Submitting two tasks then GET /tasks should return at least two results."""
    for _ in range(2):
        await client.post(
            "/tasks",
            json={"task_type": "generate", "model": "llama3:8b", "client_id": "test-client"},
        )

    resp = await client.get("/tasks")
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


async def test_get_nonexistent_task(client: AsyncClient) -> None:
    """GET /tasks/{id} for an unknown ID should return 404."""
    resp = await client.get("/tasks/bad-id-that-does-not-exist")
    assert resp.status_code == 404


async def test_unauthenticated_request_from_remote(tmp_path: Path) -> None:
    """A request with no Authorization header from a remote IP should return 401."""
    config_path = tmp_path / "ringmaster.yaml"
    config_path.write_text("")

    app, _ = await create_app(config_path, db_path=tmp_path / "test.db")

    transport = ASGITransport(app=app, client=("192.168.1.100", 12345))
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/tasks")
    assert resp.status_code == 401


async def test_localhost_skips_auth(tmp_path: Path) -> None:
    """Requests from localhost should bypass auth — no token needed."""
    config_path = tmp_path / "ringmaster.yaml"
    config_path.write_text("")

    app, _ = await create_app(config_path, db_path=tmp_path / "test.db")

    transport = ASGITransport(app=app, client=("127.0.0.1", 12345))
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/tasks")
    assert resp.status_code == 200
