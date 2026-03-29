"""systemd sleep/shutdown inhibitor lock management.

Ringmaster must prevent the workstation from sleeping or shutting down while a
GPU inference task is in progress.  The standard Linux mechanism for this is a
systemd inhibitor lock, acquired by running `systemd-inhibit` as a long-lived
child process.  The lock is held for as long as the child process lives and
released the moment it is terminated.

Using a subprocess rather than the D-Bus API directly keeps this module free of
D-Bus library dependencies while still being correct — systemd's documentation
explicitly endorses this pattern for services that do not link against libsystemd.
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)

# How long to wait (seconds) for a graceful terminate before force-killing.
_TERMINATE_TIMEOUT_SECONDS = 5


class SleepInhibitor:
    """Hold a systemd sleep/shutdown inhibitor lock for the duration of a task.

    The lock is implemented as a `systemd-inhibit` child process.  While the
    process is alive, systemd will delay (block) any sleep or shutdown request.
    The lock is released by terminating the child process.

    Usage::

        inhibitor = SleepInhibitor()
        inhibitor.acquire("running inference")
        try:
            # ... do work ...
        finally:
            inhibitor.release()
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None

    @property
    def is_held(self) -> bool:
        """True if the inhibitor lock is currently held (child process is alive)."""
        if self._proc is None:
            return False
        # poll() returns None while the process is still running
        return self._proc.poll() is None

    def acquire(self, reason: str) -> None:
        """Acquire the sleep/shutdown inhibitor lock.

        If the lock is already held this is a no-op — we never spawn more than
        one child process, avoiding lock leaks if acquire() is called redundantly.

        Args:
            reason: Human-readable description of why sleep should be blocked,
                    shown in `systemd-inhibit --list` output.
        """
        if self.is_held:
            logger.debug("Inhibitor lock already held; skipping duplicate acquire.")
            return

        cmd = [
            "systemd-inhibit",
            "--what=sleep:shutdown",
            "--who=Ringmaster",
            f"--why={reason}",
            "--mode=block",
            "sleep",
            "infinity",
        ]

        try:
            self._proc = subprocess.Popen(cmd)
            logger.info("Acquired sleep inhibitor lock: %s", reason)
        except FileNotFoundError:
            # systemd-inhibit is not available (e.g. non-systemd host or CI).
            # Log a warning but do not crash — power management is best-effort.
            logger.warning(
                "systemd-inhibit not found; sleep inhibitor lock not acquired. "
                "The workstation may sleep during task execution."
            )

    def release(self) -> None:
        """Release the inhibitor lock by terminating the child process.

        If no lock is held this is a no-op.  If the process does not exit
        gracefully within the timeout it is force-killed to avoid leaving a
        zombie that holds the lock indefinitely.
        """
        if self._proc is None:
            return

        try:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=_TERMINATE_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "systemd-inhibit did not exit after %ds; force-killing.",
                    _TERMINATE_TIMEOUT_SECONDS,
                )
                self._proc.kill()
                self._proc.wait()
            logger.info("Released sleep inhibitor lock.")
        finally:
            self._proc = None
