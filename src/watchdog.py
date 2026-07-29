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

CWD note: the very first thing this module does (before any other imports or
path resolution) is chdir to its own directory.  This guarantees that every
subsequent path built from __file__ is correct regardless of how the process
was started — e.g. via the Windows Run registry key, which launches with no
defined working directory (often defaults to System32).
"""

from __future__ import annotations   # must precede all other statements

# ── Step 0: Pin CWD to this script's directory ────────────────────────────────
# Must be the first executable code (right after __future__) so that every
# subsequent open(), FileHandler, Popen, and Path(__file__).parent call works
# correctly regardless of launch context (e.g. the Windows Run key provides no
# defined CWD and often defaults to System32 — every relative path breaks).
import os as _os
from pathlib import Path as _Path
_os.chdir(_Path(__file__).resolve().parent)
# ─────────────────────────────────────────────────────────────────────────────

import os
import sys
import time
import logging
import subprocess
import psutil
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths — all absolute, anchored to this script's location.
# Using .resolve() guards against symlinks and any leftover relative components.
# ---------------------------------------------------------------------------
_HERE     = Path(__file__).resolve().parent
_LOG_FILE = _HERE / "watchdog.log"
_CRASH_LOG = _HERE / "watchdog_crash.log"

# ---------------------------------------------------------------------------
# Startup crash guard — wraps the ENTIRE module-level setup so that any
# import error, missing-dependency crash, or path failure that occurs before
# the main loop is running gets written to an absolute-path file.
# Under pythonw.exe there is no console, so without this the process dies
# completely invisibly.
# ---------------------------------------------------------------------------
def _write_startup_crash(exc: BaseException) -> None:
    """Write a startup crash to watchdog_crash.log (absolute path)."""
    import traceback as _tb
    import datetime as _dt
    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sep = "=" * 70
    entry = (
        f"\n{sep}\n"
        f"WATCHDOG STARTUP CRASH  {ts}\n"
        f"{sep}\n"
        f"{_tb.format_exc()}"
        f"{sep}\n"
    )
    try:
        with open(_CRASH_LOG, "a", encoding="utf-8") as fh:
            fh.write(entry)
    except OSError:
        pass  # absolute nothing we can do here


# ---------------------------------------------------------------------------
# Logging -- always write to file (absolute path); add stdout only when in
# a real console.
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
TRACKER_EXE_NAME = "quota-tracker.exe" # used for orphan-kill by name
CRASH_LIMIT      = 5    # stop relaunching after this many consecutive rapid crashes
CRASH_WINDOW_S   = 10   # a crash within this many seconds of launch counts as rapid

# ---------------------------------------------------------------------------
# Tracker command — all paths are absolute, built from _HERE.
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    _TRACKER_EXE = _HERE / "quota-tracker.exe"
    _TRACKER_CMD = [str(_TRACKER_EXE)]
else:
    _TRACKER_SCRIPT = _HERE / "main.py"   # src/main.py — absolute
    # Use pythonw.exe (GUI subsystem, no console) to launch the tracker.
    #
    # Why pythonw and not python.exe?
    # - python.exe is a console app. When spawned by pythonw (no console),
    #   Windows creates a NEW console window — the ugly black terminal flash.
    # - pythonw.exe is a GUI app. When spawned by pythonw, no console window
    #   is ever created, but the interactive desktop is fully inherited so
    #   Shell_NotifyIcon (pystray tray icon) works correctly.
    # - stdout/stderr are redirected to tracker.log, so nothing is lost.
    _pythonw_exe = Path(sys.executable).parent / "pythonw.exe"
    if not _pythonw_exe.exists():
        _pythonw_exe = Path(sys.executable).parent / "python.exe"  # fallback
    _TRACKER_CMD = [str(_pythonw_exe), str(_TRACKER_SCRIPT)]

_MY_PID = os.getpid()
log.info(f"Watchdog started PID={_MY_PID}  cwd={os.getcwd()!r}")
log.info(f"_HERE={_HERE}")
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
    """Spawn the tracker. Returns the Popen handle, or None on failure.

    No special creationflags are used here intentionally.

    When the watchdog (pythonw.exe, no console) spawns python.exe with no
    flags, the child inherits the parent's window station and interactive
    desktop — which is required for Shell_NotifyIcon to show the tray icon.
    No console window will flash because pythonw.exe has no console to
    pass down. CREATE_NO_WINDOW and DETACHED_PROCESS both restrict the
    desktop context and cause the tray icon to register but never appear.

    stdout/stderr are redirected to tracker.log so there is no visible
    console even if the fallback interpreter is python.exe.

    cwd is explicitly set to _HERE (the src/ directory) so that main.py's
    own __file__-relative paths resolve correctly regardless of what CWD
    the watchdog inherited from the Windows Run key.
    """
    log.info("Launching tracker...")
    tracker_log = _HERE / "tracker.log"   # absolute path
    try:
        with open(tracker_log, "a", encoding="utf-8") as fout:
            proc = subprocess.Popen(
                _TRACKER_CMD,
                stdout=fout,
                stderr=fout,
                cwd=str(_HERE),   # pin tracker's CWD to src/ — same guarantee
                # No creationflags — child inherits interactive desktop from
                # the pythonw.exe watchdog, which is what pystray needs.
            )
        log.info(f"Tracker launched PID={proc.pid}  (output -> tracker.log)")
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


def kill_tracker_by_name() -> None:
    """Kill any orphaned quota-tracker.exe processes not owned by this watchdog.

    Called when Antigravity closes and tracker_proc is None (e.g. the watchdog
    was restarted after a build and lost its Popen handle, but a stale tracker
    process is still running).
    """
    killed = 0
    for p in psutil.process_iter(["name", "pid"]):
        try:
            if (p.info["name"] or "").lower() == TRACKER_EXE_NAME.lower():
                log.info(f"Killing orphaned tracker PID={p.pid} by name...")
                proc = psutil.Process(p.pid)
                children = proc.children(recursive=True)
                for ch in children:
                    try:
                        ch.terminate()
                    except Exception:
                        pass
                proc.terminate()
                _, alive = psutil.wait_procs([proc] + children, timeout=3)
                for ap in alive:
                    try:
                        ap.kill()
                    except Exception:
                        pass
                killed += 1
        except (psutil.NoSuchProcess, ProcessLookupError):
            pass
        except Exception as exc:
            log.debug(f"kill_by_name error: {exc}")
    if killed:
        log.info(f"Orphan kill: terminated {killed} stale tracker process(es)")


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
    crash_count    = 0      # consecutive rapid crashes
    _last_launch_t = 0.0    # time of last tracker launch (for crash window check)

    log.info(f"Polling every {POLL_INTERVAL}s  close_debounce={CLOSE_DEBOUNCE}")

    while True:
        try:
            ag_now = antigravity_running()

            if ag_now and not ag_was_running:
                # ---- Rising edge: Antigravity just appeared ----------------
                close_count = 0
                crash_count = 0
                log.info("Antigravity opened. Waiting 3 s for CDP port...")
                time.sleep(3)
                if tracker_proc is None or not is_alive(tracker_proc):
                    tracker_proc = launch_tracker()
                    _last_launch_t = time.monotonic()
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
                    # Also kill any orphaned tracker processes (e.g. if watchdog
                    # was restarted and lost its Popen handle).
                    kill_tracker_by_name()
                    tracker_proc = None
                    close_count  = 0
                    crash_count  = 0

            else:
                # ---- Antigravity stably open ------------------------------
                close_count = 0
                # Relaunch if tracker died unexpectedly while AG is open,
                # but give up after CRASH_LIMIT consecutive rapid crashes.
                if tracker_proc is not None and not is_alive(tracker_proc):
                    elapsed = time.monotonic() - _last_launch_t
                    if elapsed < CRASH_WINDOW_S:
                        crash_count += 1
                    else:
                        crash_count = 1   # reset — this crash happened long after launch

                    if crash_count >= CRASH_LIMIT:
                        log.error(
                            f"Tracker crashed {crash_count} times rapidly "
                            f"(exit={tracker_proc.returncode}). "
                            "Giving up relaunching — check tracker.log for errors."
                        )
                        tracker_proc = None
                        # Don't reset crash_count here so we don't loop again;
                        # it resets on the next rising edge (AG reopen).
                    else:
                        log.warning(
                            f"Tracker died unexpectedly (exit={tracker_proc.returncode}, "
                            f"crash {crash_count}/{CRASH_LIMIT}). "
                            "Check tracker.log for details. Relaunching..."
                        )
                        tracker_proc = launch_tracker()
                        _last_launch_t = time.monotonic()

            ag_was_running = ag_now

        except Exception as exc:
            log.error(f"Main loop error: {exc}", exc_info=True)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    # ── Top-level crash guard ──────────────────────────────────────────────
    # Any exception that escapes main() (including startup failures like a
    # missing psutil import) is written to watchdog_crash.log at an absolute
    # path before we exit.  Under pythonw.exe this is the ONLY way to see
    # what went wrong — there is no console, no dialog, nothing else.
    try:
        main()
    except KeyboardInterrupt:
        log.info("Watchdog stopped by KeyboardInterrupt")
    except Exception as exc:
        _write_startup_crash(exc)
        log.error(f"Watchdog crashed: {exc}", exc_info=True)
        raise
    except BaseException as exc:
        _write_startup_crash(exc)
        raise
