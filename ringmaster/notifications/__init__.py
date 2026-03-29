"""Notification providers for Ringmaster.

Ringmaster can alert the operator when tasks complete, fail, or require
interactive approval.  Multiple backends are supported so that the same
notification call works on a desktop workstation (D-Bus) and is also
forwarded to a Home Assistant instance for mobile push.

Usage::

    from ringmaster.notifications.desktop import DesktopNotifier
    from ringmaster.notifications.homeassistant import HANotifier

Both classes implement the :class:`~ringmaster.notifications.base.NotificationProvider`
interface, so they can be swapped or stacked transparently.
"""
