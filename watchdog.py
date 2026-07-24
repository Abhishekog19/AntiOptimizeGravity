"""
watchdog.py -- Antigravity Quota Tracker Auto-Launcher / Auto-Stopper

Runs silently at Windows startup (via pythonw.exe, no console window).
  - Antigravity opens  -> launches the tracker (python.exe main.py)
  - Antigravity closes -> kills the tracker (after CLOSE_DEBOUNCE consecutive
                          absent polls, so brief Antigravity restarts do not
                          cause flapping)

Design: tracks the subprocess.Popen handle directly instead of scanning all
processes on every poll -- this eliminates false-positive detection that was
causing the tracker to be killed immediately after launch.

Log: watchdog.log in the same directory as this script.
"""
from __future__ import annotations

import os
import sys
import time
import logging
import subprocess
import psutil
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE     = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
_LOG_FILE = _HERE / "watchdog.log"

# ---------------------------------------------------------------------------
# Logging -- always write to file; add stdout only when in a real console
# ---------------------------------------------------------------------------
_handlers: list = [logging.FileHandler(_LOG_FILE, encoding="utf-8", mode="a")]
try:
    if sys.stdout is not None:
        sys.stdout.fileno()          # raises OSError if pythonw.exe
        _handlers.append(logging.StreamHandler(sys.stdout))
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=_handlers,
)
log = logging.getLogger("watchdog")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
POLL_INTERVAL    = 3     # seconds between process checks
CLOSE_DEBOUNCE   = 3     # consecutive absent polls before killing tracker
ANTIGRAVITY_NAME = "antigravity ide"   # substring match on process name (lower)

# ---------------------------------------------------------------------------
# Tracker command
# Always use python.exe (NOT pythonw.exe) to launch main.py.
# pythonw.exe suppresses the Windows message pump that pystray needs
# to register the tray icon in the notification area.
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    _TRACKER_EXE = _HERE / "quota-tracker.exe"
    _TRACKER_CMD = [str(_TRACKER_EXE)]
else:
    _TRACKER_SCRIPT = _HERE / "main.py"
    _python_exe = Path(sys.executable).parent / "python.exe"
    if not _python_exe.exists():
        _python_exe = Path(sys.executable)
    _TRACKER_CMD = [str(_python_exe), str(_TRACKER_SCRIPT)]

_MY_PID = os.getpid()
log.info(f"Watchdog started PID={_MY_PID}")
log.info(f"Tracker cmd: {_TRACKER_CMD}")


# ---------------------------------------------------------------------------
# Antigravity detection
# ---------------------------------------------------------------------------

def antigravity_running() -> bool:
    """True if any Antigravity IDE process is alive."""
    try:
        return any(
            ANTIGRAVITY_NAME in (p.info["name"] or "").lower()
            for p in psutil.process_iter(["name"])
            if p.pid != _MY_PID
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Tracker lifecycle -- tracked by Popen handle, not by process scanning
# ---------------------------------------------------------------------------

def launch_tracker() -> "subprocess.Popen | None":
    """Spawn the tracker. Returns the Popen handle, or None on failure."""
    log.info(f"Launching tracker...")
    try:
        proc = subprocess.Popen(
            _TRACKER_CMD,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        log.info(f"Tracker launched PID={proc.pid}")
        return proc
    except Exception as exc:
        log.error(f"Failed to launch tracker: {exc}")
        return None


def kill_tracker(proc: "subprocess.Popen") -> None:
    """Terminate the tracker and all its child processes."""
    log.info(f"Killing tracker PID={proc.pid}...")
    try:
        parent = psutil.Process(proc.pid)
        children = parent.children(recursive=True)
        for ch in children:
            try:
                ch.terminate()
            except Exception:
                pass
        parent.terminate()
        _, alive = psutil.wait_procs([parent] + children, timeout=3)
        for p in alive:
            try:
                p.kill()
            except Exception:
                pass
    except (psutil.NoSuchProcess, ProcessLookupError):
        pass   # already dead -- that is fine
    try:
        proc.wait(timeout=1)
    except Exception:
        pass
    log.info("Tracker killed")


def is_alive(proc: "subprocess.Popen") -> bool:
    """Check whether our launched subprocess is still running."""
    try:
        return proc.poll() is None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    tracker_proc: "subprocess.Popen | None" = None
    ag_was_running = False
    close_count    = 0      # consecutive polls where AG is absent

    log.info(f"Polling every {POLL_INTERVAL}s  close_debounce={CLOSE_DEBOUNCE}")

    while True:
        try:
            ag_now = antigravity_running()

            if ag_now and not ag_was_running:
                # ---- Rising edge: Antigravity just appeared ----------------
                close_count = 0
                log.info("Antigravity opened. Waiting 3 s for CDP port...")
                time.sleep(3)
                if tracker_proc is None or not is_alive(tracker_proc):
                    tracker_proc = launch_tracker()
                else:
                    log.info(f"Tracker still alive (PID={tracker_proc.pid}), skipping")

            elif not ag_now and ag_was_running:
                # ---- Falling edge: first poll where AG is absent -----------
                close_count = 1
                log.info(f"Antigravity gone (debounce {close_count}/{CLOSE_DEBOUNCE})")

            elif not ag_now and not ag_was_running and close_count > 0:
                # ---- Still absent (debounce counting) ---------------------
                close_count += 1
                log.info(f"Antigravity still gone (debounce {close_count}/{CLOSE_DEBOUNCE})")
                if close_count >= CLOSE_DEBOUNCE:
                    if tracker_proc is not None and is_alive(tracker_proc):
                        kill_tracker(tracker_proc)
                    tracker_proc = None
                    close_count  = 0

            else:
                # ---- Antigravity stably open ------------------------------
                close_count = 0
                # Relaunch if tracker died unexpectedly while AG is open
                if tracker_proc is not None and not is_alive(tracker_proc):
                    log.warning(f"Tracker died unexpectedly (exit={tracker_proc.returncode}), relaunching...")
                    tracker_proc = launch_tracker()

            ag_was_running = ag_now

        except Exception as exc:
            log.error(f"Main loop error: {exc}", exc_info=True)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
