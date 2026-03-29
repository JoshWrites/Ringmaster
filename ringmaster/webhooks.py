"""Webhook delivery with retry and exponential backoff.

Ringmaster uses fire-and-forget webhooks: when a task finishes, it POSTs a
WebhookPayload to the caller's callback_url.  Network conditions are
unpredictable, so delivery is retried with exponential backoff before giving
up.  The caller is responsible for idempotent handling on their end (e.g.
deduplication by task_id), since the same payload may be delivered more than
once if a retry succeeds after an in-flight request that the remote already
processed.

This module is intentionally narrow: it does one thing (deliver a payload to
a URL) and is easy to unit-test by mocking httpx responses.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from ringmaster.models import WebhookPayload

logger = logging.getLogger(__name__)

# Total time to wait for a remote server to respond.  30 s is long enough to
# tolerate a slow or overloaded webhook consumer while still bounding the
# worst-case delay per attempt to something reasonable.
_REQUEST_TIMEOUT_SECONDS = 30.0


async def deliver_webhook(
    url: str | None,
    payload: WebhookPayload,
    *,
    max_retries: int = 3,
    base_delay: float = 2.0,
) -> bool:
    """POST a WebhookPayload to url, retrying on failure with exponential backoff.

    Args:
        url: The HTTP(S) endpoint to deliver to.  If None, the call is a no-op
            and True is returned immediately — this lets callers pass
            task.callback_url directly without a guard clause at every call site.
        payload: The WebhookPayload to serialise and POST as JSON.
        max_retries: Maximum number of attempts before giving up.  Each failed
            attempt counts: the first failure is attempt 1, so with max_retries=3
            the payload is tried at most 3 times total.
        base_delay: Seconds to wait before the first retry.  Subsequent retries
            wait base_delay * 2^attempt, so the delays are base_delay, 2×, 4×, …
            This bounds the thundering-herd effect when many tasks finish at once.

    Returns:
        True if the server responded with a 2xx status code on any attempt, or
        if url is None.  False if all attempts were exhausted without success.
    """
    if url is None:
        # No callback URL was registered for this task — nothing to do.
        return True

    body = payload.model_dump_json()

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
        for attempt in range(1, max_retries + 1):
            try:
                response = await client.post(
                    url,
                    content=body,
                    headers={"Content-Type": "application/json"},
                )
                if response.is_success:
                    logger.info(
                        "Webhook delivered: task_id=%s url=%s status=%d",
                        payload.task_id,
                        url,
                        response.status_code,
                    )
                    return True

                # Non-2xx response (e.g. 500, 429) — treat as retriable.
                logger.warning(
                    "Webhook attempt %d/%d failed: task_id=%s url=%s status=%d",
                    attempt,
                    max_retries,
                    payload.task_id,
                    url,
                    response.status_code,
                )

            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                logger.warning(
                    "Webhook attempt %d/%d error: task_id=%s url=%s error=%r",
                    attempt,
                    max_retries,
                    payload.task_id,
                    url,
                    exc,
                )

            # Don't sleep after the last attempt — we're about to return False.
            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1))
                await asyncio.sleep(delay)

    logger.error(
        "Webhook delivery exhausted after %d attempts: task_id=%s url=%s",
        max_retries,
        payload.task_id,
        url,
    )
    return False
