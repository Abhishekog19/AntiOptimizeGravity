#!/usr/bin/env python3
"""
main.py - Antigravity Quota Tracker v4.0
Single entry point: python main.py

Architecture
------------
  Thread 1   Flask server (daemon)        -> http://localhost:4300
  Thread 2   CDP watcher (daemon)         -> 2 triggers: launch + GetTurnDiff
  Main thread  pystray tray icon          -> left-click = open dashboard
  Subprocess   webview_launcher.py        -> native WebView2/WKWebView window

All threads share the app_state singleton (state.py) for live status.
"""
from __future__ import annotations   # must be first statement after docstring

# ── PyInstaller packaged-exe WebView launcher early-exit ──────────────────────
# When the tray opens the dashboard it spawns:
#   AntigravityQuotaTracker.exe --webview-launcher http://localhost:4300
# We intercept that flag BEFORE any pystray / tray logic starts so webview
# can own the process main thread without conflicting with pystray.
import sys
if "--webview-launcher" in sys.argv:
    _idx = sys.argv.index("--webview-launcher")
    _url = sys.argv[_idx + 1] if _idx + 1 < len(sys.argv) else "http://localhost:4300"
    import webview as _wv

    # Parse optional --x / --y position args forwarded from tray_icon.py
    def _get_arg(name):
        try:
            i = sys.argv.index(name)
            return int(sys.argv[i + 1])
        except (ValueError, IndexError):
            return None

    _wv_kwargs = dict(
        title="Quota Tracker",
        url=_url,
        width=420,
        height=600,
        resizable=True,
        min_size=(360, 480),
        background_color="#0b0f1a",
    )
    _wx = _get_arg("--x")
    _wy = _get_arg("--y")
    if _wx is not None:
        _wv_kwargs["x"] = _wx
    if _wy is not None:
        _wv_kwargs["y"] = _wy

    _wv.create_window(**_wv_kwargs)
    _wv.start()
    sys.exit(0)
# ─────────────────────────────────────────────────────────────────────────────

import os
import threading
import time
import logging
import signal
from pathlib import Path

# ── Ensure project root AND notifier dir are on sys.path ─────────────────────
_ROOT = Path(__file__).resolve().parent
_NOTIFIER_DIR = _ROOT / "notifier"
for _p in [str(_ROOT), str(_NOTIFIER_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Load .env files ───────────────────────────────────────────────────────────
def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if k not in os.environ:
            os.environ[k] = v.strip().strip('"').strip("'")

_load_env(_ROOT / "notifier" / ".env")
_load_env(_ROOT / "dashboard" / ".env")

# ── Logging setup ─────────────────────────────────────────────────────────────
_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("main")
logging.getLogger("werkzeug").setLevel(logging.WARNING)

DRY_RUN = "--dry-run" in sys.argv
VERBOSE  = "--verbose" in sys.argv or _LOG_LEVEL == "DEBUG"
if VERBOSE:
    logging.getLogger().setLevel(logging.DEBUG)

# ── Crash log (written before any thread or logger has a chance to die) ───────
import traceback as _traceback_mod
import datetime as _dt

_CRASH_LOG = _ROOT / "crash_log.txt"


def _crash_guard(thread_name: str, fn, *args, **kwargs):
    """
    Call fn(*args, **kwargs) inside an outermost exception barrier.

    On any uncaught exception (including BaseException subclasses that escape
    inner try/except blocks like SystemExit, KeyboardInterrupt, etc.):
      1. Formats the full traceback via traceback.format_exc()
      2. Appends it to crash_log.txt in the project root (synchronous write,
         no reliance on the logging subsystem)
      3. Prints it to stderr so it appears in the console
      4. Re-raises the original exception so the thread dies normally

    This is applied at the outermost level of every long-lived thread so that
    crashes which escape all inner try/except blocks leave evidence behind.
    """
    try:
        fn(*args, **kwargs)
    except KeyboardInterrupt:
        raise   # let Ctrl-C propagate normally
    except Exception:
        _write_crash(thread_name)
        raise
    except BaseException:
        # Catches SystemExit, GeneratorExit, etc. — write log then re-raise
        _write_crash(thread_name)
        raise


def _write_crash(thread_name: str) -> None:
    """Write a timestamped crash entry to crash_log.txt and stderr."""
    tb  = _traceback_mod.format_exc()
    ts  = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sep = "=" * 70
    entry = (
        f"\n{sep}\n"
        f"CRASH  {ts}  thread={thread_name}\n"
        f"{sep}\n"
        f"{tb}"
        f"{sep}\n"
    )
    # Write to file — open in append mode so multiple crashes accumulate
    try:
        with open(_CRASH_LOG, "a", encoding="utf-8") as fh:
            fh.write(entry)
    except OSError:
        pass   # can't write the log — at least print it
    # Always print to stderr (visible in terminal even if logger is broken)
    print(entry, file=sys.stderr, flush=True)


def _write_event(label: str, detail: str = "") -> None:
    """
    Append a timestamped non-crash event marker to crash_log.txt.
    Used for startup and clean-exit markers so the file always exists
    and we can distinguish 'started but killed externally' from 'crashed'
    from 'exited cleanly'.
    """
    ts  = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sep = "-" * 70
    entry = f"{sep}\n{label}  {ts}  pid={os.getpid()}  {detail}\n"
    try:
        with open(_CRASH_LOG, "a", encoding="utf-8") as fh:
            fh.write(entry)
    except OSError:
        pass


# Write startup marker immediately — this guarantees crash_log.txt exists
# so we can tell 'file never written' from 'program killed externally'.
_write_event("STARTED", f"argv={sys.argv!r}")

import atexit as _atexit
_atexit.register(lambda: _write_event("CLEAN EXIT"))

# ── Shared state ──────────────────────────────────────────────────────────────
from state import app_state


# ── Windows startup auto-registration ────────────────────────────────────────

def _register_windows_startup() -> None:
    """
    Register watchdog.py (not main.py) in the Windows startup registry.

    The watchdog runs at boot, watches for Antigravity IDE to open, and
    launches the tracker automatically. This keeps startup lean: the tracker
    only consumes resources while Antigravity is actually running.

    Migration: also removes any stale AntigravityQuotaTracker entry that
    registered main.py directly in older versions.
    """
    if sys.platform != "win32":
        return
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        watchdog_name = "AntigravityQuotaWatchdog"
        tracker_name  = "AntigravityQuotaTracker"   # legacy — remove if present

        if getattr(sys, "frozen", False):
            # Running as a packaged .exe — the watchdog exe lives alongside us
            watchdog_exe = Path(sys.executable).parent / "quota-watchdog.exe"
            cmd = f'"{watchdog_exe}"'
        else:
            # Running from source
            watchdog_script = _ROOT / "watchdog.py"
            cmd = f'"{sys.executable}" "{watchdog_script}"'

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path,
            0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
        )

        # Remove stale legacy entry (main.py registered at startup)
        try:
            winreg.DeleteValue(key, tracker_name)
            log.info(f"Removed legacy startup entry: {tracker_name}")
        except FileNotFoundError:
            pass

        # Check if watchdog entry is already correct
        try:
            existing, _ = winreg.QueryValueEx(key, watchdog_name)
            if existing == cmd:
                winreg.CloseKey(key)
                return
        except FileNotFoundError:
            pass

        winreg.SetValueEx(key, watchdog_name, 0, winreg.REG_SZ, cmd)
        winreg.CloseKey(key)
        log.info(f"Registered watchdog in Windows startup: {cmd}")
    except Exception as exc:
        log.debug(f"Could not register startup: {exc}")


# ── First-run Antigravity detection ──────────────────────────────────────────

def _check_antigravity_installed() -> None:
    """
    Check whether Antigravity IDE is installed in any standard location.
    Runs once at startup (in a daemon thread, after a short delay).

    If Antigravity is not found, logs a clear warning with actionable steps
    so users on a fresh machine get guidance rather than a confusing blank state.
    Does NOT crash or block startup.
    """
    import time as _time
    _time.sleep(2.0)   # let Flask + tray initialise first

    candidates = []
    if sys.platform == "win32":
        localappdata = os.environ.get("LOCALAPPDATA", "")
        programfiles = os.environ.get("PROGRAMFILES", "")
        pfiles_x86   = os.environ.get("PROGRAMFILES(X86)", "")
        candidates = [
            Path(localappdata) / "Programs" / "Antigravity IDE" / "Antigravity IDE.exe",
            Path(localappdata) / "Programs" / "Antigravity"     / "Antigravity.exe",
            Path(programfiles) / "Antigravity IDE" / "Antigravity IDE.exe",
            Path(pfiles_x86)   / "Antigravity IDE" / "Antigravity IDE.exe",
        ]
    elif sys.platform == "darwin":
        home = Path.home()
        candidates = [
            Path("/Applications/Antigravity IDE.app"),
            Path("/Applications/Antigravity.app"),
            home / "Applications" / "Antigravity IDE.app",
            home / "Applications" / "Antigravity.app",
        ]

    found = any(p.exists() for p in candidates if p != Path(""))
    if not found:
        log.warning(
            "Antigravity IDE not found in standard install locations. "
            "If it is installed elsewhere, the tracker will still work — "
            "but if this is a first install, download Antigravity IDE and "
            "then run the setup script: "
            "  Windows: powershell -ExecutionPolicy Bypass -File scripts\\setup-windows.ps1 "
            "  Mac:     bash scripts/setup-mac.sh"
        )
        app_state.log(
            "Antigravity IDE not found in standard locations — "
            "run setup script after installing it.",
            app_state.LEVEL_WARN,
        )
    else:
        log.debug("Antigravity IDE found in standard install location.")


# ── Flask server thread ───────────────────────────────────────────────────────

def _start_flask() -> None:
    try:
        from server.flask_app import run_flask
        port = int(os.environ.get("PORT", "4300"))
        log.info(f"Starting Flask dashboard on http://localhost:{port}")
        run_flask(host="0.0.0.0", port=port, debug=False)
    except Exception as exc:
        log.error(f"Flask server crashed: {exc}", exc_info=True)
        raise   # let crash_guard catch it at the outer level


def _start_flask_guarded() -> None:
    _crash_guard("Flask", _start_flask)


# ── CDP watcher thread ────────────────────────────────────────────────────────

def _start_watcher() -> None:
    time.sleep(1.5)   # let Flask boot first
    try:
        from notifier import run_watcher
        log.info("Starting CDP watcher (triggers: launch + GetTurnDiff)")
        run_watcher()
    except Exception as exc:
        log.error(f"CDP watcher crashed: {exc}", exc_info=True)
        app_state.log(f"Watcher crashed: {exc}", app_state.LEVEL_ERROR)
        raise   # let crash_guard catch it at the outer level


def _start_watcher_guarded() -> None:
    _crash_guard("Watcher", _start_watcher)


# ── Graceful shutdown ─────────────────────────────────────────────────────────

_shutdown_event = threading.Event()


def _quit() -> None:
    log.info("Quit requested - shutting down...")
    _shutdown_event.set()
    sys.exit(0)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("-" * 50)
    log.info("Antigravity Quota Tracker v4.0")
    mode = "DRY-RUN" if DRY_RUN else "LIVE"
    log.info(f"Mode: {mode}  |  Dashboard: http://localhost:4300")
    log.info("-" * 50)

    app_state.log("Starting...", app_state.LEVEL_INFO)

    # Auto-register in Windows startup
    threading.Thread(target=_register_windows_startup, daemon=True, name="StartupReg").start()

    # First-run Antigravity install check
    threading.Thread(target=_check_antigravity_installed, daemon=True, name="InstallCheck").start()

    # Thread 1: Flask
    threading.Thread(target=_start_flask_guarded, daemon=True, name="Flask").start()

    # Thread 2: CDP watcher
    threading.Thread(target=_start_watcher_guarded, daemon=True, name="Watcher").start()

    # Seed account cache
    def _seed_accounts():
        time.sleep(1.0)
        try:
            from server.db import list_accounts_with_latest
            accounts = list_accounts_with_latest()
            app_state.set_accounts(accounts)
            log.info(f"Account cache seeded: {len(accounts)} account(s)")
        except Exception as exc:
            log.warning(f"Could not seed account cache: {exc}")
    threading.Thread(target=_seed_accounts, daemon=True, name="AccountSeed").start()

    from tray.tray_icon import TrayIcon
    from notifier import fire_capture

    def _manual_capture():
        fire_capture("manual_tray", needs_refresh=True)

    tray = TrayIcon(
        fire_capture_fn=_manual_capture,
        quit_fn=_quit,
    )

    signal.signal(signal.SIGINT,  lambda s, f: _quit())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda s, f: _quit())

    log.info("Tray icon starting...")
    log.info(">>> Right-click the coloured dot in the system tray for menu")
    log.info(">>> Click '^' near the clock if the icon is hidden")
    app_state.log("Ready - tray active", app_state.LEVEL_OK)

    log.info(f"Crash log will be written to: {_CRASH_LOG}")
    _crash_guard("TrayMain", tray.run)   # blocks main thread


if __name__ == "__main__":
    main()
