"""Tests for the desktop notification provider (ringmaster.notifications.desktop).

These tests verify two contracts:
  1. notify() delegates to dbus_notify and forwards the result when no user
     interaction is required (actions=None).
  2. notify() with actions returns the action key chosen by the user as
     reported by dbus_notify.

We mock dbus_notify at the module level so no D-Bus session bus is required
during CI.  The actual D-Bus plumbing is exercised in integration tests only.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ringmaster.notifications.desktop import DesktopNotifier


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_desktop_notification_sends():
    """notify() should invoke dbus_notify with the supplied title and message.

    We verify that the underlying D-Bus call is actually made rather than
    silently dropped — the function being a no-op on error is tested via the
    integration path, not here.
    """
    with patch(
        "ringmaster.notifications.desktop.dbus_notify",
        new_callable=AsyncMock,
        return_value=None,
    ) as mock_notify:
        notifier = DesktopNotifier()
        result = await notifier.notify("Test Title", "Test message body")

    mock_notify.assert_called_once_with("Test Title", "Test message body", actions=None)
    assert result is None


@pytest.mark.asyncio
async def test_desktop_notification_with_actions():
    """notify() should return the action key chosen by the user via dbus_notify.

    dbus_notify listens for the ActionInvoked D-Bus signal and returns the
    key of the chosen action.  DesktopNotifier must propagate that return
    value faithfully — callers use it to branch on what the user clicked.
    """
    with patch(
        "ringmaster.notifications.desktop.dbus_notify",
        new_callable=AsyncMock,
        return_value="approve",
    ) as mock_notify:
        notifier = DesktopNotifier()
        actions = {"approve": "Approve", "reject": "Reject"}
        result = await notifier.notify("Approval Required", "Do you approve?", actions=actions)

    mock_notify.assert_called_once_with(
        "Approval Required", "Do you approve?", actions=actions
    )
    assert result == "approve"
