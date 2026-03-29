"""Home Assistant notification provider.

Sends notifications to a Home Assistant instance via the REST API.  HA's
notify service is one-way (fire-and-forget): it delivers the message to
configured targets (mobile app, persistent notification, etc.) but does not
relay any user interaction back to the caller.  HANotifier therefore always
returns None from :meth:`notify`.

Any actions passed in will be included in the message body as a plain-text
appendix so the recipient still sees the available options, even though
clicking them from an HA notification does nothing on the Ringmaster side.

This provider is useful for alerting the operator's phone when Ringmaster is
running headlessly (e.g. as a systemd service) and there is no desktop session
for D-Bus notifications.
"""

from __future__ import annotations

import logging

import httpx

from ringmaster.notifications.base import NotificationProvider

logger = logging.getLogger(__name__)

# Seconds to wait for HA to respond before giving up.  HA is typically on the
# local network and should respond in well under a second; 10 s is generous.
_REQUEST_TIMEOUT_SECONDS = 10.0


class HANotifier(NotificationProvider):
    """Sends notifications to Home Assistant via the notify service REST API.

    Args:
        ha_url: Base URL of the HA instance, e.g. ``http://ha.local:8123``.
            Must not have a trailing slash.
        ha_token: Long-lived access token generated in HA → Profile → Security.
            Used as a Bearer token in the Authorization header.
    """

    def __init__(self, ha_url: str, ha_token: str) -> None:
        self._url = f"{ha_url}/api/services/notify/notify"
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {ha_token}",
                "Content-Type": "application/json",
            },
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )

    async def notify(
        self,
        title: str,
        message: str,
        actions: dict[str, str] | None = None,
    ) -> None:
        """POST a notification to the HA notify service.

        HA's notify service does not support interactive responses, so this
        method always returns None.  Any supplied actions are appended to the
        message body as a plain-text list so the recipient can see them.

        The call is best-effort: ConnectError and TimeoutException are caught,
        logged as warnings, and silently absorbed.  Notification failures must
        not propagate to the caller.

        Args:
            title: Short summary shown as the notification heading.
            message: Body text.  If actions are provided they are appended
                as a plain-text list: ``\n\nActions: approve, reject``.
            actions: Optional action map.  Included in the body for
                informational purposes only — HA cannot relay clicks back.

        Returns:
            Always None (HA does not support interactive responses).
        """
        body = message
        if actions:
            action_labels = ", ".join(actions.values())
            body = f"{message}\n\nActions: {action_labels}"

        payload = {"title": title, "message": body}

        try:
            response = await self._client.post(self._url, json=payload)
            if not response.is_success:
                logger.warning(
                    "HA notification returned non-2xx status: %d %s",
                    response.status_code,
                    response.text,
                )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning("HA notification failed (network error): %r", exc)

        return None

    async def close(self) -> None:
        """Release the underlying HTTP connection pool.

        Call this when the notifier is no longer needed to avoid leaving
        open connections.  Typically called on application shutdown.
        """
        await self._client.aclose()
