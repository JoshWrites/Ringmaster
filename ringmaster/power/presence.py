"""User presence detection for Ringmaster.

Before sending an approval notification or auto-approving a queued task,
Ringmaster needs to know whether a human is currently at the keyboard.  This
module queries the system for how long the input devices have been idle.

Currently only the `xprintidle` method is implemented because it is the most
portable option for X11 desktops and requires no D-Bus dependencies.  A future
version may add a `dbus` method using org.freedesktop.ScreenSaver.GetSessionIdleTime
for Wayland compatibility.

The safe default on any error is to assume the user IS present.  This prevents
Ringmaster from silently auto-approving tasks while someone is actively working —
it is always better to send an unwanted notification than to miss a wanted one.
"""

from __future__ import annotations

import asyncio
import logging

from ringmaster.config import IdleConfig

logger = logging.getLogger(__name__)


class PresenceDetector:
    """Detect whether the user is currently at the keyboard.

    The detection method and idle threshold are read from the operator-supplied
    IdleConfig so that Ringmaster can be tuned per-installation without code
    changes.

    Args:
        config: Idle detection configuration from the main Ringmaster config.
    """

    def __init__(self, config: IdleConfig) -> None:
        self._config = config

    async def is_user_present(self) -> bool:
        """Return True if the user appears to be at the keyboard.

        Uses the detection method specified in IdleConfig.  Currently supports:

        - ``xprintidle``: Reads idle time from the X11 input subsystem by
          running the ``xprintidle`` binary and parsing its millisecond output.

        For any unrecognised detection method, or on any runtime error, this
        method returns True (the safe default: assume user is present).

        Returns:
            True if the session idle time is below the configured threshold,
            or if the idle time cannot be determined.
        """
        method = self._config.detection_method

        if method == "xprintidle":
            return await self._check_xprintidle()

        # For 'dbus', 'none', or any future method not yet implemented, fall
        # through to the safe default so that unknown configurations do not
        # silently enable auto-approval.
        logger.debug(
            "Presence detection method %r not implemented; assuming user is present.",
            method,
        )
        return True

    async def _check_xprintidle(self) -> bool:
        """Query X11 idle time via the xprintidle binary.

        xprintidle reports the number of milliseconds since the last input
        event on any X11 input device.  We compare this against the configured
        threshold (in seconds) to decide presence.

        Returns:
            True if idle_ms < threshold_seconds * 1000, or True on any error.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "xprintidle",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            idle_ms = int(stdout.strip())
            threshold_ms = self._config.idle_threshold_seconds * 1000
            is_present = idle_ms < threshold_ms
            logger.debug(
                "xprintidle: idle=%dms threshold=%dms present=%s",
                idle_ms,
                threshold_ms,
                is_present,
            )
            return is_present
        except Exception:
            # Any failure — binary not found, parse error, X display not
            # available — falls back to the safe default.
            logger.warning(
                "xprintidle check failed; assuming user is present.",
                exc_info=True,
            )
            return True
