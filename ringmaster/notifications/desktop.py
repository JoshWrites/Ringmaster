"""Desktop notification provider via D-Bus (org.freedesktop.Notifications).

This module implements the standard freedesktop.org desktop notification
protocol using dbus-next.  It supports both fire-and-forget alerts and
interactive notifications with action buttons — for example, asking the
operator to approve or reject a high-priority task before it starts.

The public surface is two things:
  - :func:`dbus_notify` — a standalone async function that manages the full
    D-Bus handshake (connect, Notify call, signal listening).
  - :class:`DesktopNotifier` — a :class:`~ringmaster.notifications.base.NotificationProvider`
    that delegates to :func:`dbus_notify`, making it swappable with other
    backends.

Both are best-effort: any D-Bus error is logged as a warning and None is
returned, so notification failures never crash the caller.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# Notification timeout constants (milliseconds).
# A zero timeout means "stay until the user dismisses or clicks an action".
# We use a brief timeout for fire-and-forget alerts so they don't pile up.
_TIMEOUT_NO_ACTIONS_MS = 5_000
_TIMEOUT_WITH_ACTIONS_MS = 0  # stay visible until the user acts

# org.freedesktop.Notifications signal — fired when the user clicks a button
_SIGNAL_ACTION_INVOKED = "ActionInvoked"
# org.freedesktop.Notifications signal — fired when notification is dismissed
_SIGNAL_NOTIFICATION_CLOSED = "NotificationClosed"

# Reason codes returned by NotificationClosed (freedesktop spec §3.7)
_CLOSE_REASON_EXPIRED = 1
_CLOSE_REASON_DISMISSED = 2
_CLOSE_REASON_PROGRAMMATIC = 3


async def dbus_notify(
    title: str,
    message: str,
    actions: dict[str, str] | None = None,
) -> str | None:
    """Send a desktop notification over D-Bus and optionally await user input.

    Implements the org.freedesktop.Notifications.Notify flow:
      1. Connect to the session bus.
      2. Call Notify with the supplied title, body, and optional action list.
      3. If actions are present, listen for ActionInvoked/NotificationClosed
         signals and return the chosen action key (or None on dismiss/timeout).

    The function intentionally catches all exceptions and converts them to a
    logged warning + None return.  Desktop notifications are best-effort:
    the operator may not have a D-Bus session (e.g. when running under a
    system service), and that must not prevent Ringmaster from functioning.

    Args:
        title: Short summary shown as the notification heading.
        message: Longer body text with details.
        actions: Optional mapping of action key → human-readable label.
            Buttons are shown in insertion order.

    Returns:
        The key of the action chosen by the user, or None.
    """
    try:
        from dbus_next.aio import MessageBus  # type: ignore[import]
        from dbus_next import BusType  # type: ignore[import]

        bus = await MessageBus(bus_type=BusType.SESSION).connect()

        introspection = await bus.introspect(
            "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
        )
        proxy = bus.get_proxy_object(
            "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
            introspection,
        )
        interface = proxy.get_interface("org.freedesktop.Notifications")

        # Build the flat action list expected by the spec: [key, label, key, label, ...]
        action_list: list[str] = []
        if actions:
            for key, label in actions.items():
                action_list.extend([key, label])

        timeout_ms = _TIMEOUT_WITH_ACTIONS_MS if actions else _TIMEOUT_NO_ACTIONS_MS

        notification_id: int = await interface.call_notify(
            "Ringmaster",  # app_name
            0,             # replaces_id (0 = new notification)
            "",            # app_icon
            title,
            message,
            action_list,
            {},            # hints
            timeout_ms,
        )

        if not actions:
            bus.disconnect()
            return None

        # Wait for the user to click an action or dismiss the notification.
        chosen_action: str | None = None
        done_event = asyncio.Event()

        def on_action_invoked(notif_id: int, action_key: str) -> None:
            """Called when the user clicks an action button."""
            if notif_id == notification_id:
                nonlocal chosen_action
                chosen_action = action_key
                done_event.set()

        def on_notification_closed(notif_id: int, reason: int) -> None:
            """Called when the notification disappears without an action click."""
            if notif_id == notification_id:
                done_event.set()

        interface.on_action_invoked(on_action_invoked)
        interface.on_notification_closed(on_notification_closed)

        await done_event.wait()
        bus.disconnect()
        return chosen_action

    except Exception as exc:  # noqa: BLE001
        logger.warning("Desktop notification failed: %r", exc)
        return None


from ringmaster.notifications.base import NotificationProvider  # noqa: E402


class DesktopNotifier(NotificationProvider):
    """Desktop notification provider backed by :func:`dbus_notify`.

    This thin wrapper makes the standalone :func:`dbus_notify` function
    available as a :class:`~ringmaster.notifications.base.NotificationProvider`,
    so it can be used interchangeably with other backends (e.g. HANotifier)
    or combined with them in a multi-provider fan-out.
    """

    async def notify(
        self,
        title: str,
        message: str,
        actions: dict[str, str] | None = None,
    ) -> str | None:
        """Send a desktop notification via D-Bus and return any chosen action.

        Delegates entirely to :func:`dbus_notify`.  See that function's
        docstring for the full protocol description and error-handling
        behaviour.
        """
        return await dbus_notify(title, message, actions=actions)
