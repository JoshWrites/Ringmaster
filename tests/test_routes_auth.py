"""Tests for POST /auth/register and /auth/revoke route handlers.

Auth management lets operators issue and revoke bearer tokens over the API
itself — so there is a bootstrapping concern: you need an existing token to
register a new one.  These tests use a pre-registered fixture token.
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


async def test_register_client(client: AsyncClient) -> None:
    """POST /auth/register should return a new token for the given client_id."""
    resp = await client.post("/auth/register", json={"client_id": "new-client"})
    assert resp.status_code == 200
    body = resp.json()
    assert "token" in body
    assert "client_id" in body


async def test_revoke_client(client: AsyncClient) -> None:
    """Registering then POST /auth/revoke should return revoked=true."""
    reg = await client.post("/auth/register", json={"client_id": "disposable-client"})
    client_id = reg.json()["client_id"]

    resp = await client.post("/auth/revoke", json={"client_id": client_id})
    assert resp.status_code == 200
    assert resp.json()["revoked"] is True
