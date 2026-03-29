"""Tests for the PowerActions class.

PowerActions wraps shell commands for sleep, screen lock, and display blanking.
These tests mock asyncio.create_subprocess_shell so they run without a real
desktop session.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ringmaster.config import PowerConfig
from ringmaster.power.actions import PowerActions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_proc(returncode: int = 0) -> AsyncMock:
    """Return a mock subprocess that reports the given exit code."""
    proc = AsyncMock()
    proc.wait = AsyncMock(return_value=returncode)
    proc.returncode = returncode
    return proc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sleep_runs_configured_command() -> None:
    """sleep() executes the configured sleep_command via the shell.

    Verifies that the exact command string from PowerConfig is passed to
    asyncio.create_subprocess_shell.
    """
    config = PowerConfig(sleep_command="systemctl suspend")
    actions = PowerActions(config)

    mock_proc = _make_mock_proc()

    with patch("asyncio.create_subprocess_shell", return_value=mock_proc) as mock_shell:
        await actions.sleep()

    mock_shell.assert_called_once_with("systemctl suspend")


@pytest.mark.asyncio
async def test_lock_screen() -> None:
    """lock() executes the configured lock_command via the shell.

    Verifies that lock_command is dispatched correctly when set.
    """
    config = PowerConfig(lock_command="loginctl lock-session")
    actions = PowerActions(config)

    mock_proc = _make_mock_proc()

    with patch("asyncio.create_subprocess_shell", return_value=mock_proc) as mock_shell:
        await actions.lock()

    mock_shell.assert_called_once_with("loginctl lock-session")


@pytest.mark.asyncio
async def test_display_off() -> None:
    """display_off() executes the configured display_off_command via the shell.

    Verifies that display_off_command is dispatched correctly when set.
    """
    config = PowerConfig(display_off_command="xset dpms force off")
    actions = PowerActions(config)

    mock_proc = _make_mock_proc()

    with patch("asyncio.create_subprocess_shell", return_value=mock_proc) as mock_shell:
        await actions.display_off()

    mock_shell.assert_called_once_with("xset dpms force off")
