#!/usr/bin/env python3
"""
main.py - Antigravity Quota Tracker v4.0
Single entry point: python main.py

Architecture
------------
  Thread 1  Flask server (daemon)      -> http://localhost:4300
  Thread 2  CDP watcher (daemon)       -> 5 triggers, heartbeat
  Main thread  pystray tray icon       -> popup, menu
  Tk thread  tkinter popup (daemon)    -> started by QuotaPopup on first click

All three layers share the app_state singleton (state.py) for live status.

Windows startup: on first run, this script registers itself in the Windows
startup registry so it starts automatically on login.
"""

from __future__ import annotations
import sys
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

# ── Load .env from notifier/.env (same as before) ────────────────────────────
def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if k not in os.environ:   # env vars take precedence
            os.environ[k] = v.strip().strip('"').strip("'")

_load_env(_ROOT / "notifier" / ".env")

# ── Also load dashboard/.env for TESSERACT_PATH etc ──────────────────────────
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

# ── Suppress overly chatty loggers ────────────────────────────────────────────
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# ── DRY_RUN / VERBOSE flags ───────────────────────────────────────────────────
DRY_RUN = "--dry-run" in sys.argv
VERBOSE  = "--verbose" in sys.argv or _LOG_LEVEL == "DEBUG"
if VERBOSE:
    logging.getLogger().setLevel(logging.DEBUG)


# ── Shared state ──────────────────────────────────────────────────────────────
from state import app_state


# ── Windows startup auto-registration ────────────────────────────────────────

def _register_windows_startup() -> None:
    """
    Add this script (or .exe) to Windows startup registry so it runs on login.
    Silently skips on non-Windows or if already registered.
    """
    if sys.platform != "win32":
        return
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "AntigravityQuotaTracker"

        # Build the startup command
        if getattr(sys, "frozen", False):
            # Running as PyInstaller .exe
            cmd = f'"{sys.executable}"'
        else:
            # Running as python script
            cmd = f'"{sys.executable}" "{str(_ROOT / "main.py")}"'

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path,
            0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE
        )

        # Check if already registered with same command
        try:
            existing, _ = winreg.QueryValueEx(key, app_name)
            if existing == cmd:
                winreg.CloseKey(key)
                return  # already registered correctly
        except FileNotFoundError:
            pass  # not yet registered

        winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, cmd)
        winreg.CloseKey(key)
        log.info(f"Registered in Windows startup: {cmd}")
    except Exception as exc:
        log.debug(f"Could not register startup: {exc}")


# ── Flask server thread ────────────────────────────────────────────────────────

def _start_flask() -> None:
    from server.flask_app import run_flask
    port = int(os.environ.get("PORT", "4300"))
    log.info(f"Starting Flask dashboard on http://localhost:{port}")
    run_flask(host="0.0.0.0", port=port, debug=False)


# ── CDP watcher thread ─────────────────────────────────────────────────────────

def _start_watcher() -> None:
    # Small delay to let Flask start first (so heartbeats land correctly)
    time.sleep(1.5)
    try:
        from notifier import run_watcher
        log.info("Starting CDP watcher (5 triggers + debounce=2s)")
        run_watcher()
    except Exception as exc:
        log.error(f"CDP watcher crashed: {exc}", exc_info=True)
        app_state.log(f"Watcher crashed: {exc}", app_state.LEVEL_ERROR)


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

    # ── Auto-register in Windows startup ─────────────────────────────────────
    threading.Thread(
        target=_register_windows_startup,
        daemon=True,
        name="StartupReg",
    ).start()

    # ── Thread 1: Flask ───────────────────────────────────────────────────────
    flask_thread = threading.Thread(
        target=_start_flask,
        daemon=True,
        name="Flask",
    )
    flask_thread.start()

    # ── Thread 2: CDP watcher ─────────────────────────────────────────────────
    watcher_thread = threading.Thread(
        target=_start_watcher,
        daemon=True,
        name="Watcher",
    )
    watcher_thread.start()

    # ── Seed account cache from DB immediately ────────────────────────────────
    def _seed_accounts():
        time.sleep(1.0)   # give Flask a moment to init
        try:
            from server.db import list_accounts_with_latest
            accounts = list_accounts_with_latest()
            app_state.set_accounts(accounts)
            log.info(f"Account cache seeded: {len(accounts)} account(s) in DB")
        except Exception as exc:
            log.warning(f"Could not seed account cache: {exc}")
    threading.Thread(target=_seed_accounts, daemon=True, name="AccountSeed").start()

    # ── Wire up tray icon + popup ─────────────────────────────────────────────
    from tray.tray_icon import TrayIcon
    from tray.popup import QuotaPopup
    from notifier import fire_capture

    def _manual_capture():
        fire_capture("manual_tray", needs_refresh=True)

    popup = QuotaPopup(fire_capture_fn=_manual_capture)

    tray = TrayIcon(
        popup_toggle_fn=popup.toggle,
        fire_capture_fn=_manual_capture,
        quit_fn=_quit,
    )

    # ── CTRL+C handler ────────────────────────────────────────────────────────
    signal.signal(signal.SIGINT, lambda s, f: _quit())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda s, f: _quit())

    log.info("Tray icon starting...")
    log.info(">>> Look for the coloured dot in the system tray (near the clock)")
    log.info(">>> If hidden: click the ^ arrow in the taskbar to find it")
    app_state.log("Ready - tray active", app_state.LEVEL_OK)

    # tray.run() blocks the main thread (required by pystray on Windows/macOS)
    tray.run()


if __name__ == "__main__":
    main()
