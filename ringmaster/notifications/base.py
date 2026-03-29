"""Abstract base class for notification providers.

Every notification backend (desktop D-Bus, Home Assistant, etc.) must
implement :class:`NotificationProvider`.  The common interface lets callers
dispatch notifications without caring which backend is active, and makes it
straightforward to fan out to multiple providers simultaneously.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class NotificationProvider(ABC):
    """Common interface for all Ringmaster notification backends.

    Implementations are expected to be best-effort: a notification that
    cannot be delivered (e.g. because the D-Bus session has gone away, or
    the HA instance is unreachable) should log a warning and return None
    rather than raising an exception.  The calling code must never depend
    on a notification being delivered.
    """

    @abstractmethod
    async def notify(
        self,
        title: str,
        message: str,
        actions: dict[str, str] | None = None,
    ) -> str | None:
        """Send a notification and optionally wait for user interaction.

        Args:
            title: Short summary line shown as the notification heading.
            message: Longer body text with details.
            actions: Optional mapping of action key → human-readable label.
                When provided, the notification should display buttons and
                the method should block until the user clicks one (or the
                notification times out).  The dict preserves insertion order
                so the buttons appear in the same order the caller specified.

        Returns:
            The key of the action chosen by the user, or None if:
              * no actions were specified,
              * the notification timed out without user interaction,
              * the backend does not support interactive responses (e.g. HA),
              * the notification could not be delivered.
        """
