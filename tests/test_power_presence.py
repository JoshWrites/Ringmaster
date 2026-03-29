"""Tests for the PresenceDetector class.

PresenceDetector queries the system for user idle time to decide whether a
human is at the keyboard.  These tests mock the subprocess layer so they run
without a real X display or xprintidle binary.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ringmaster.config import IdleConfig
from ringmaster.power.presence import PresenceDetector


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_presence_detector_idle_check() -> None:
    """600 000 ms reported idle time exceeds the 300 s threshold → not present.

    xprintidle returns milliseconds; the threshold is in seconds.  We verify
    that the unit conversion is applied correctly so that 600 s idle > 300 s
    threshold yields is_user_present() == False.
    """
    config = IdleConfig(detection_method="xprintidle", idle_threshold_seconds=300)
    detector = PresenceDetector(config)

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"600000\n", b""))
    mock_proc.returncode = 0

    with patch(
        "asyncio.create_subprocess_exec", return_value=mock_proc
    ):
        result = await detector.is_user_present()

    assert result is False


@pytest.mark.asyncio
async def test_presence_detector_active_user() -> None:
    """5 000 ms reported idle time is below the 300 s threshold → user present.

    A freshly active user will have a very small idle time.  We confirm that
    is_user_present() returns True when idle_ms < threshold_seconds * 1000.
    """
    config = IdleConfig(detection_method="xprintidle", idle_threshold_seconds=300)
    detector = PresenceDetector(config)

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"5000\n", b""))
    mock_proc.returncode = 0

    with patch(
        "asyncio.create_subprocess_exec", return_value=mock_proc
    ):
        result = await detector.is_user_present()

    assert result is True


@pytest.mark.asyncio
async def test_presence_detector_fallback_on_error() -> None:
    """Any error from xprintidle causes is_user_present() to return True.

    Returning True on failure is the safe default: it prevents Ringmaster from
    auto-approving tasks when we cannot confirm the user is away.  Better to
    interrupt someone who is present than to silently run tasks when they might
    not want them.
    """
    config = IdleConfig(detection_method="xprintidle", idle_threshold_seconds=300)
    detector = PresenceDetector(config)

    with patch(
        "asyncio.create_subprocess_exec", side_effect=FileNotFoundError("xprintidle not found")
    ):
        result = await detector.is_user_present()

    assert result is True
