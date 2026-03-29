"""Tests for the Home Assistant notification provider (ringmaster.notifications.homeassistant).

These tests verify two contracts:
  1. notify() sends an authenticated POST request to the HA notify service
     endpoint with a Bearer token in the Authorization header.
  2. notify() swallows ConnectError (and similar transport failures) rather
     than propagating them — notifications are best-effort and should never
     crash the caller.

We use pytest-httpx to intercept outgoing httpx requests so no real HA
instance is needed.
"""

from __future__ import annotations

import pytest
import httpx
from pytest_httpx import HTTPXMock

from ringmaster.notifications.homeassistant import HANotifier


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ha_notify_sends_request(httpx_mock: HTTPXMock):
    """notify() should POST to the HA notify service with a Bearer token.

    The Authorization header is the only security mechanism between Ringmaster
    and HA, so we verify it is present and correctly formatted.
    """
    httpx_mock.add_response(
        method="POST",
        url="http://ha.local:8123/api/services/notify/notify",
        status_code=200,
    )

    notifier = HANotifier(
        ha_url="http://ha.local:8123",
        ha_token="test-token-abc",
    )
    try:
        result = await notifier.notify("Alert", "Something happened")
    finally:
        await notifier.close()

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert "Bearer test-token-abc" in requests[0].headers["Authorization"]
    # HA notifications never return an interactive response
    assert result is None


@pytest.mark.asyncio
async def test_ha_notify_handles_failure(httpx_mock: HTTPXMock):
    """notify() should not raise when HA is unreachable.

    Notifications are informational and best-effort.  A network failure must
    not propagate to the caller — it should be logged and silently absorbed.
    """
    httpx_mock.add_exception(httpx.ConnectError("connection refused"))

    notifier = HANotifier(
        ha_url="http://ha.unreachable:8123",
        ha_token="test-token-xyz",
    )
    try:
        # This must not raise — that's the entire contract being tested.
        result = await notifier.notify("Alert", "Something happened")
    finally:
        await notifier.close()

    assert result is None
