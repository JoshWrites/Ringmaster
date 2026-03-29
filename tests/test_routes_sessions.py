"""Tests for POST/GET/DELETE /sessions route handlers.

Sessions represent GPU reservations for interactive inference.  These tests
verify the open, retrieve, keepalive, and close lifecycle against a real
in-process SQLite database.
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


async def test_open_session(client: AsyncClient) -> None:
    """POST /sessions should create a session and return 201 with id and open status."""
    resp = await client.post(
        "/sessions",
        json={"model": "llama3:8b", "client_id": "test-client"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "id" in body
    assert body["status"] == "open"


async def test_get_session(client: AsyncClient) -> None:
    """Opening a session then GET /sessions/{id} should return 200."""
    open_resp = await client.post(
        "/sessions",
        json={"model": "llama3:8b", "client_id": "test-client"},
    )
    session_id = open_resp.json()["id"]

    resp = await client.get(f"/sessions/{session_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == session_id


async def test_close_session(client: AsyncClient) -> None:
    """DELETE /sessions/{id} should mark the session closed; subsequent GET reflects that."""
    open_resp = await client.post(
        "/sessions",
        json={"model": "llama3:8b", "client_id": "test-client"},
    )
    session_id = open_resp.json()["id"]

    delete_resp = await client.delete(f"/sessions/{session_id}")
    assert delete_resp.status_code == 200

    get_resp = await client.get(f"/sessions/{session_id}")
    assert get_resp.json()["status"] == "closed"


async def test_keepalive_session(client: AsyncClient) -> None:
    """POST /sessions/{id}/keepalive should return 200 and update last_activity_at."""
    open_resp = await client.post(
        "/sessions",
        json={"model": "llama3:8b", "client_id": "test-client"},
    )
    session_id = open_resp.json()["id"]

    resp = await client.post(f"/sessions/{session_id}/keepalive")
    assert resp.status_code == 200

    # After keepalive, last_activity_at should be set.
    session = await client.get(f"/sessions/{session_id}")
    assert session.json()["last_activity_at"] is not None
