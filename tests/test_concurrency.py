"""Tests for concurrent access safety.

These tests verify that the server handles multiple simultaneous requests
without database corruption or state inconsistency.
"""

from __future__ import annotations

from pathlib import Path

import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from ringmaster.server.app import create_app


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncClient:
    """Create a fully-wired FastAPI app with a registered test client."""
    config_path = tmp_path / "ringmaster.yaml"
    config_path.write_text("")

    app, auth_manager = await create_app(config_path, db_path=tmp_path / "test.db")
    raw_token = auth_manager.register("test-client")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {raw_token}"},
    ) as ac:
        yield ac


async def test_concurrent_task_submissions(client: AsyncClient) -> None:
    """Submitting many tasks concurrently via HTTP must not corrupt the database."""
    import asyncio

    async def submit(i: int) -> int:
        resp = await client.post(
            "/tasks",
            json={
                "task_type": "generate",
                "model": "llama3",
                "prompt": f"task-{i}",
                "client_id": "test-client",
            },
        )
        return resp.status_code

    results = await asyncio.gather(*[submit(i) for i in range(20)])
    assert all(code == 201 for code in results), f"Some submissions failed: {results}"

    resp = await client.get("/tasks")
    tasks = resp.json()
    assert len(tasks) == 20


async def test_concurrent_session_opens(client: AsyncClient) -> None:
    """Opening many sessions concurrently must not corrupt the database."""
    import asyncio

    async def open_session(i: int) -> int:
        resp = await client.post(
            "/sessions",
            json={"model": "llama3", "client_id": "test-client"},
        )
        return resp.status_code

    results = await asyncio.gather(*[open_session(i) for i in range(10)])
    assert all(code == 201 for code in results), f"Some opens failed: {results}"
