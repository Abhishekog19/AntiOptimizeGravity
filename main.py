#!/usr/bin/env python3
"""
main.py — Antigravity Quota Tracker v4.0
Single entry point: python main.py

Architecture
────────────
  Thread 1  Flask server (daemon)      → http://localhost:4300
  Thread 2  CDP watcher (daemon)       → 5 triggers, heartbeat
  Main thread  pystray tray icon       → popup, menu
  Tk thread  tkinter popup (daemon)    → started by QuotaPopup on first click

All three layers share the app_state singleton (state.py) for live status.
"""

from __future__ import annotations
import sys
import os
import threading
import time
import logging
import signal
from pathlib import Path

# ── Ensure project root is on sys.path ────────────────────────────────────────
_ROOT = Path(__file__).parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

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


# ── Flask server thread ────────────────────────────────────────────────────────

def _start_flask() -> None:
    from server.flask_app import run_flask
    port = int(os.environ.get("PORT", "4300"))
    log.info(f"Starting Flask dashboard → http://localhost:{port}")
    run_flask(host="0.0.0.0", port=port, debug=False)


# ── CDP watcher thread ─────────────────────────────────────────────────────────

def _start_watcher() -> None:
    # Small delay to let Flask start first (so heartbeats land correctly)
    time.sleep(1.5)
    try:
        # Make notifier inherit the DRY_RUN/VERBOSE flags from argv
        from notifier.notifier import run_watcher
        log.info("Starting CDP watcher (5 triggers active)")
        run_watcher()
    except Exception as exc:
        log.error(f"CDP watcher crashed: {exc}", exc_info=True)
        app_state.log(f"Watcher crashed: {exc}", app_state.LEVEL_ERROR)


# ── Graceful shutdown ─────────────────────────────────────────────────────────

_shutdown_event = threading.Event()


def _quit() -> None:
    log.info("Quit requested — shutting down…")
    _shutdown_event.set()
    sys.exit(0)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("━" * 50)
    log.info("Antigravity Quota Tracker v4.0")
    mode = "DRY-RUN" if DRY_RUN else "LIVE"
    log.info(f"Mode: {mode}  |  Dashboard: http://localhost:4300")
    log.info("━" * 50)

    app_state.log("Antigravity Quota Tracker starting…", app_state.LEVEL_INFO)

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

    # ── Seed account cache from DB (so popup has data immediately) ────────────
    def _seed_accounts():
        time.sleep(2.0)
        try:
            from server.db import list_accounts_with_latest
            app_state.set_accounts(list_accounts_with_latest())
            log.debug("Account cache seeded from DB")
        except Exception as exc:
            log.warning(f"Could not seed account cache: {exc}")
    threading.Thread(target=_seed_accounts, daemon=True, name="AccountSeed").start()

    # ── Wire up tray icon + popup ─────────────────────────────────────────────
    from tray.tray_icon import TrayIcon
    from tray.popup import QuotaPopup
    from notifier.notifier import fire_capture

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

    log.info("Tray icon starting (main thread)…")
    app_state.log("Ready — tray icon active", app_state.LEVEL_OK)

    # tray.run() blocks the main thread (required by pystray on Windows/macOS)
    tray.run()


if __name__ == "__main__":
    main()
