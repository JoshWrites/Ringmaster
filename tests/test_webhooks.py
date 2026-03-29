"""Tests for the webhook delivery utility (ringmaster.webhooks).

These tests verify three contracts:
  1. A successful HTTP response (2xx) causes deliver_webhook to return True
     and make exactly one HTTP call.
  2. A persistent connection error triggers retries up to max_retries and
     ultimately returns False when all attempts are exhausted.
  3. Passing url=None is treated as a no-op success (returns True, no HTTP
     call) — this lets callers pass callback_url directly without a None
     guard at the call site.

We use pytest-httpx to intercept all outgoing httpx requests so no real
network traffic is generated.  base_delay is set to 0.01 in retry tests to
keep the test suite fast without disabling the backoff logic entirely.
"""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from ringmaster.models import WebhookPayload
from ringmaster.webhooks import deliver_webhook


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_payload() -> WebhookPayload:
    """A minimal completed-task payload used across multiple tests."""
    return WebhookPayload(
        task_id="task-abc",
        status="completed",
        result="Hello, world!",
        model="llama3",
        gpu_used="rtx4090",
        duration_seconds=1.23,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliver_webhook_success(httpx_mock: HTTPXMock, sample_payload: WebhookPayload):
    """A 200 OK response should result in True returned, with exactly one POST."""
    httpx_mock.add_response(
        method="POST",
        url="https://example.com/callback",
        status_code=200,
    )

    result = await deliver_webhook("https://example.com/callback", sample_payload)

    assert result is True
    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url == "https://example.com/callback"


@pytest.mark.asyncio
async def test_deliver_webhook_retries_on_failure(
    httpx_mock: HTTPXMock, sample_payload: WebhookPayload
):
    """Persistent ConnectError should exhaust all retries and return False.

    We verify that exactly max_retries attempts are made (not max_retries + 1)
    by counting intercepted requests.  base_delay=0.01 keeps the test fast
    while still exercising the retry/backoff code path.
    """
    import httpx

    max_retries = 3

    # Register one ConnectError response per expected attempt
    for _ in range(max_retries):
        httpx_mock.add_exception(httpx.ConnectError("connection refused"))

    result = await deliver_webhook(
        "https://unreachable.example.com/callback",
        sample_payload,
        max_retries=max_retries,
        base_delay=0.01,
    )

    assert result is False
    assert len(httpx_mock.get_requests()) == max_retries


@pytest.mark.asyncio
async def test_deliver_webhook_skips_when_no_url(
    httpx_mock: HTTPXMock, sample_payload: WebhookPayload
):
    """url=None should return True immediately without making any HTTP call.

    This allows callers to pass task.callback_url directly, even when the
    task was submitted without a callback URL.
    """
    result = await deliver_webhook(None, sample_payload)

    assert result is True
    # No HTTP requests should have been issued
    assert len(httpx_mock.get_requests()) == 0
