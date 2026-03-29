"""Tests for GET /health, /status, /gpus, and /models route handlers.

Status endpoints provide observability: liveness probes, system state at a
glance, GPU configuration, and available Ollama models.  /health intentionally
skips auth so external monitors can probe it without a token.
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


async def test_health(client: AsyncClient) -> None:
    """GET /health should return alive=true and a version string."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["alive"] is True
    assert "version" in body


async def test_health_no_auth(tmp_path: Path) -> None:
    """/health must be reachable without a bearer token — it's the liveness probe."""
    config_path = tmp_path / "ringmaster.yaml"
    config_path.write_text("")

    app, _ = await create_app(config_path, db_path=tmp_path / "test.db")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/health")
    assert resp.status_code == 200


async def test_status(client: AsyncClient) -> None:
    """GET /status should include state, queue_depth, user_present, and queue_paused."""
    resp = await client.get("/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "state" in body
    assert "queue_depth" in body
    assert "user_present" in body
    assert "queue_paused" in body
