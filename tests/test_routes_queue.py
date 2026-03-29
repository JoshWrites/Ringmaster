"""Tests for POST /queue/pause, /resume, and /drain route handlers.

Queue lifecycle control lets operators pause dispatch, resume it, or request a
graceful drain before a planned power event.  These tests verify the response
payloads and flag transitions.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from ringmaster.server.app import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_pause_queue(client: AsyncClient) -> None:
    """POST /queue/pause should return queue_paused=true."""
    resp = await client.post("/queue/pause")
    assert resp.status_code == 200
    assert resp.json()["queue_paused"] is True


async def test_resume_queue(client: AsyncClient) -> None:
    """Pausing then POST /queue/resume should return queue_paused=false."""
    await client.post("/queue/pause")

    resp = await client.post("/queue/resume")
    assert resp.status_code == 200
    assert resp.json()["queue_paused"] is False


async def test_drain_queue(client: AsyncClient) -> None:
    """POST /queue/drain should return 200 with draining=true."""
    resp = await client.post("/queue/drain")
    assert resp.status_code == 200
    assert resp.json()["draining"] is True
