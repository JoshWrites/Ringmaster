"""Power management for Ringmaster.

This subpackage handles three distinct concerns:

  1. **Inhibitor** (`inhibitor.py`): Hold a systemd sleep/shutdown inhibitor
     lock while a task is actively running, preventing the workstation from
     sleeping mid-inference.

  2. **Presence** (`presence.py`): Detect whether a user is at the keyboard by
     querying the X11 idle timer.  Used to decide whether to send an approval
     notification or to auto-approve a queued task.

  3. **Actions** (`actions.py`): Execute the shell commands that actually
     change power state — sleep, screen lock, and display blanking.  All
     commands are operator-configured so Ringmaster stays desktop-agnostic.
"""
