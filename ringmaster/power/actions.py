"""Power state actions for Ringmaster.

This module provides a thin async wrapper around the shell commands that change
the workstation's power state.  All commands are operator-configured in
PowerConfig, which keeps Ringmaster desktop-agnostic — the same binary works
on KDE, GNOME, i3, Sway, or any other environment as long as the operator
provides the appropriate commands.

Commands are executed via asyncio.create_subprocess_shell so that compound
shell expressions (e.g. ``xset dpms force off && notify-send "display off"``)
work correctly without requiring separate argument tokenisation.

Methods silently skip execution (with a warning) when the corresponding command
is not configured.  This allows partial configurations — e.g. configuring only
the sleep command while leaving lock and display_off unset — without errors.
"""

from __future__ import annotations

import asyncio
import logging

from ringmaster.config import PowerConfig

logger = logging.getLogger(__name__)


class PowerActions:
    """Execute shell commands that change the workstation power state.

    Args:
        config: Power management configuration from the main Ringmaster config.
                All command fields are optional; unset commands are silently
                skipped.
    """

    def __init__(self, config: PowerConfig) -> None:
        self._config = config

    async def _run(self, command: str) -> int:
        """Run a shell command and return its exit code.

        Uses asyncio.create_subprocess_shell so that compound expressions with
        pipes, redirects, and shell builtins all work as the operator expects.

        Args:
            command: Shell command string to execute.

        Returns:
            The process exit code.
        """
        proc = await asyncio.create_subprocess_shell(command)
        return await proc.wait()

    async def sleep(self) -> None:
        """Send the workstation to sleep using the configured sleep_command.

        No-op (with a warning) if sleep_command is not configured.
        """
        if not self._config.sleep_command:
            logger.warning("sleep() called but sleep_command is not configured; skipping.")
            return
        logger.info("Executing sleep command: %s", self._config.sleep_command)
        await self._run(self._config.sleep_command)

    async def lock(self) -> None:
        """Lock the screen using the configured lock_command.

        No-op (with a warning) if lock_command is not configured.
        """
        if not self._config.lock_command:
            logger.warning("lock() called but lock_command is not configured; skipping.")
            return
        logger.info("Executing lock command: %s", self._config.lock_command)
        await self._run(self._config.lock_command)

    async def display_off(self) -> None:
        """Blank the display using the configured display_off_command.

        No-op (with a warning) if display_off_command is not configured.
        Blanking the display without locking is useful for screensaver-style
        behaviour where the session should remain unlocked for background tasks.
        """
        if not self._config.display_off_command:
            logger.warning(
                "display_off() called but display_off_command is not configured; skipping."
            )
            return
        logger.info("Executing display-off command: %s", self._config.display_off_command)
        await self._run(self._config.display_off_command)

    async def lock_and_blank(self) -> None:
        """Lock the screen and then blank the display.

        Combines lock() and display_off() for the common pattern of securing
        the session before powering down the monitor.  Each step is skipped
        independently if its command is not configured.
        """
        await self.lock()
        await self.display_off()
