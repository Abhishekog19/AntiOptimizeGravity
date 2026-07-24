"""
watchdog.py -- Antigravity Quota Tracker Auto-Launcher

Runs silently at Windows startup. Watches for Antigravity IDE to open,
then launches the tracker. Uses ~2 MB RAM and zero CPU when idle.

This process is registered in Windows startup (not main.py).
It wakes every POLL_INTERVAL seconds, checks whether Antigravity IDE is
running, and launches the tracker the moment it appears -- without any
terminal window. The tray icon is the only visible output.

Works in two modes:
  Source mode   (python watchdog.py)          -> launches main.py
  Packaged mode (quota-watchdog.exe)          -> launches quota-tracker.exe
                                                 from the same directory
"""
from __future__ import annotations

import sys
import time
import subprocess
import psutil
from pathlib import Path

POLL_INTERVAL    = 3          # seconds between process checks
ANTIGRAVITY_NAME = "antigravity ide"   # lowercase substring match against process name

# -- Resolve the tracker to launch -------------------------------------------
# When frozen (packaged as quota-watchdog.exe), launch quota-tracker.exe from
# the same directory.  When running from source, launch main.py via the current
# Python interpreter.

_HERE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent

if getattr(sys, "frozen", False):
    _TRACKER_CMD = [str(_HERE / "quota-tracker.exe")]
else:
    _TRACKER_CMD = [sys.executable, str(_HERE / "main.py")]


# -- Process detection --------------------------------------------------------

def antigravity_running() -> bool:
    """Return True if any process whose name contains 'antigravity ide' is alive."""
    try:
        return any(
            ANTIGRAVITY_NAME in (p.info["name"] or "").lower()
            for p in psutil.process_iter(["name"])
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def tracker_running() -> bool:
    """Return True if the quota tracker is already running."""
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            name    = (p.info.get("name") or "").lower()
            cmdline = " ".join(p.info.get("cmdline") or []).lower()
            # Packaged exe
            if "quota-tracker" in name:
                return True
            # Source mode: main.py inside the project directory
            if "main.py" in cmdline and "antigravityoptimizer" in cmdline:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


# -- Tracker launch -----------------------------------------------------------

def launch_tracker() -> None:
    """Spawn the tracker with no terminal window."""
    subprocess.Popen(
        _TRACKER_CMD,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


# -- Main loop ----------------------------------------------------------------

def main() -> None:
    ag_was_running = False

    while True:
        ag_running = antigravity_running()

        # Rising edge: Antigravity just appeared
        if ag_running and not ag_was_running:
            # Give Antigravity ~3 s to fully initialise (CDP port needs time to open)
            time.sleep(3)
            if not tracker_running():
                launch_tracker()

        ag_was_running = ag_running
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
