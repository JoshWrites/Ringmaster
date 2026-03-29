"""Tests for the SleepInhibitor class.

SleepInhibitor wraps a `systemd-inhibit` child process to hold a sleep/shutdown
inhibitor lock while Ringmaster is processing a task.  These tests mock the
subprocess layer so they run without a real systemd installation.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from ringmaster.power.inhibitor import SleepInhibitor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_proc(is_alive: bool = True) -> MagicMock:
    """Return a mock subprocess.Popen object with a configurable liveness state."""
    proc = MagicMock()
    proc.poll.return_value = None if is_alive else 0
    return proc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_inhibitor_acquire_and_release() -> None:
    """Acquiring the inhibitor spawns a child process; releasing terminates it.

    Verifies:
    - is_held is False before acquire
    - is_held is True after acquire (process alive)
    - release() calls terminate() on the child process
    - is_held is False after release (process no longer alive)
    """
    inhibitor = SleepInhibitor()
    assert inhibitor.is_held is False

    mock_proc = _make_mock_proc(is_alive=True)

    with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
        inhibitor.acquire("running inference task")

    mock_popen.assert_called_once()
    assert inhibitor.is_held is True

    # Simulate the process dying after terminate
    mock_proc.poll.return_value = 0
    inhibitor.release()

    mock_proc.terminate.assert_called_once()


def test_inhibitor_double_acquire_is_noop() -> None:
    """Calling acquire() a second time while the lock is held is a no-op.

    We must not spawn a second child process — that would leak an orphaned
    systemd-inhibit process if Ringmaster restarts or crashes.
    """
    inhibitor = SleepInhibitor()
    mock_proc = _make_mock_proc(is_alive=True)

    with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
        inhibitor.acquire("first task")
        inhibitor.acquire("second task — should not spawn a new process")

    # Popen must have been called exactly once despite two acquire() calls
    assert mock_popen.call_count == 1


def test_inhibitor_release_without_acquire_is_noop() -> None:
    """Calling release() when no lock is held does not raise an exception.

    This can happen during shutdown or error recovery paths where acquire()
    was never called or already failed.
    """
    inhibitor = SleepInhibitor()
    # Should not raise
    inhibitor.release()
