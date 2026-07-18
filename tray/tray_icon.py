"""
tray/tray_icon.py - System tray icon for Antigravity Quota Tracker

Icon colour reflects worst-case Claude weekly quota remaining:
  Green  all accounts > 30%
  Amber  at least one <= 30%
  Red    all accounts <= 10% (or no data)

Left-click  -> activates default menu item = "Open Dashboard"
Right-click -> full menu: Open Dashboard | Capture Now | Quit

PyWebView fix (Bug D):
  webview.start() requires the process main thread. pystray occupies the main
  thread. Solution: spawn webview_launcher.py as a fresh subprocess each time.
  That process owns its own main thread — no conflict.

Left-click fix:
  On Windows, pystray's on_activate fires on DOUBLE-click only.
  Single left-click activates the item with default=True in the menu.
  We set default=True on "Open Dashboard" to make single left-click work.
"""

from __future__ import annotations
import sys
import os
import threading
import time
import logging
import webbrowser
import subprocess
from typing import Optional, Callable

try:
    import pystray
    from pystray import MenuItem as Item, Menu
    _HAS_PYSTRAY = True
except ImportError:
    _HAS_PYSTRAY = False

try:
    from PIL import Image, ImageDraw
    _HAS_PILLOW = True
except ImportError:
    _HAS_PILLOW = False

log = logging.getLogger(__name__)

# ── Icon colours ──────────────────────────────────────────────────────────────
_COLOR_GREEN = (74,  222, 128, 255)
_COLOR_AMBER = (251, 191,  36, 255)
_COLOR_RED   = (248, 113, 113, 255)
_COLOR_GREY  = (90,  90,   90, 255)
_ICON_SIZE   = 64

_DASHBOARD_URL = "http://localhost:4300"

# Resolve the webview_launcher.py path once at import time.
_LAUNCHER_SCRIPT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "webview_launcher.py")
)


def _make_icon_image(color: tuple) -> "Image.Image":
    img  = Image.new("RGBA", (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad  = 6
    draw.ellipse([pad, pad, _ICON_SIZE - pad, _ICON_SIZE - pad], fill=color)
    draw.ellipse(
        [pad - 2, pad - 2, _ICON_SIZE - pad + 2, _ICON_SIZE - pad + 2],
        outline=(255, 255, 255, 50), width=1,
    )
    return img


def _compute_icon_color(accounts: list) -> tuple:
    if not accounts:
        return _COLOR_GREY
    pcts = [
        a["latest"]["claude_weekly_pct"]
        for a in accounts
        if a.get("latest") and a["latest"].get("claude_weekly_pct") is not None
    ]
    if not pcts:
        return _COLOR_GREY
    min_pct = min(pcts)
    if min_pct <= 10:
        return _COLOR_RED
    if min_pct <= 30:
        return _COLOR_AMBER
    return _COLOR_GREEN


# ── Dashboard window opener ───────────────────────────────────────────────────

def _open_dashboard_window(url: str = _DASHBOARD_URL) -> None:
    """
    Spawn webview_launcher.py as a fresh subprocess so it gets its own
    main thread (required by WebView2/WKWebView). Falls back to the default
    browser if anything goes wrong.
    """
    try:
        if getattr(sys, "frozen", False):
            # PyInstaller .exe — invoke self with special flag
            proc = subprocess.Popen([sys.executable, "--webview-launcher", url])
        else:
            proc = subprocess.Popen([sys.executable, _LAUNCHER_SCRIPT, url])

        # Give the process ~2 s to start; if it exits immediately it crashed
        time.sleep(2.0)
        rc = proc.poll()
        if rc is not None:
            log.warning(
                f"webview_launcher exited immediately (code={rc}). "
                "Opening in browser instead."
            )
            webbrowser.open(url)
        else:
            log.info("Dashboard window opened (PyWebView subprocess running)")

    except FileNotFoundError:
        log.warning("webview_launcher.py not found — opening in browser")
        webbrowser.open(url)
    except Exception as exc:
        log.warning(f"Could not open dashboard window: {exc} — opening in browser")
        webbrowser.open(url)


# ── TrayIcon ──────────────────────────────────────────────────────────────────

class TrayIcon:
    """
    Wraps pystray.Icon.
    Call run() on the MAIN thread (required on Windows and macOS).

    Left-click  = activates the default menu item (Open Dashboard)
    Right-click = full menu
    """

    def __init__(
        self,
        fire_capture_fn: Optional[Callable] = None,
        quit_fn:         Optional[Callable] = None,
    ) -> None:
        self._fire_capture  = fire_capture_fn
        self._quit_fn       = quit_fn
        self._icon: Optional["pystray.Icon"] = None
        self._current_color = _COLOR_GREY

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Start the tray icon (blocks the main thread)."""
        if not _HAS_PYSTRAY or not _HAS_PILLOW:
            log.warning("pystray or Pillow not installed — dashboard at %s", _DASHBOARD_URL)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
            return

        # KEY FIX: set default=True on "Open Dashboard" so that a SINGLE
        # LEFT-CLICK on the tray icon activates it (not just double-click).
        menu = Menu(
            Item(
                "Open Dashboard",
                self._on_open_dashboard,
                default=True,       # ← single left-click triggers this
            ),
            Item("Capture Now", self._on_capture_now),
            Menu.SEPARATOR,
            Item("Quit", self._on_quit),
        )

        self._icon = pystray.Icon(
            name="antigravity-quota-tracker",
            icon=_make_icon_image(_COLOR_GREY),
            title="Antigravity Quota Tracker — starting...",
            menu=menu,
        )

        threading.Thread(target=self._startup_sequence, daemon=True, name="TrayStartup").start()
        log.info("Tray icon running. Left-click = Open Dashboard | Right-click = menu")
        self._icon.run()   # blocks

    def update_icon(self, accounts: list) -> None:
        if not self._icon:
            return
        color = _compute_icon_color(accounts)
        if color == self._current_color:
            return
        self._current_color = color
        labels = {
            _COLOR_GREEN: "All accounts healthy",
            _COLOR_AMBER: "Some accounts running low",
            _COLOR_RED:   "Accounts near-empty!",
            _COLOR_GREY:  "No data yet",
        }
        try:
            self._icon.icon  = _make_icon_image(color)
            self._icon.title = f"Antigravity Quota Tracker — {labels.get(color, '')}"
        except Exception as exc:
            log.debug("Icon update failed: %s", exc)

    def notify(self, title: str, message: str) -> None:
        try:
            from win10toast import ToastNotifier
            ToastNotifier().show_toast(title, message, duration=8, threaded=True)
        except Exception:
            pass

    def stop(self) -> None:
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass

    # ── Handlers ───────────────────────────────────────────────────────────────

    def _on_open_dashboard(self, icon=None, item=None) -> None:
        """Spawn the dashboard window in a daemon thread so pystray doesn't block."""
        threading.Thread(
            target=_open_dashboard_window,
            daemon=True,
            name="OpenDashboard",
        ).start()

    def _on_capture_now(self, icon=None, item=None) -> None:
        if self._fire_capture:
            threading.Thread(
                target=self._fire_capture,
                daemon=True,
                name="TrayCapture",
            ).start()

    def _on_quit(self, icon=None, item=None) -> None:
        if self._icon:
            self._icon.stop()
        if self._quit_fn:
            self._quit_fn()

    # ── Background loops ────────────────────────────────────────────────────────

    def _startup_sequence(self) -> None:
        time.sleep(1.5)
        self._notify_startup()
        try:
            from state import app_state
            self.update_icon(app_state.get_accounts())
        except Exception:
            pass
        self._icon_update_loop()

    def _icon_update_loop(self) -> None:
        try:
            from state import app_state
        except Exception:
            return
        while True:
            try:
                self.update_icon(app_state.get_accounts())
            except Exception as exc:
                log.debug("Icon update error: %s", exc)
            time.sleep(30)

    def _notify_startup(self) -> None:
        try:
            from win10toast import ToastNotifier
            ToastNotifier().show_toast(
                "Antigravity Quota Tracker",
                "Running in system tray. Left-click the dot to open dashboard.",
                duration=6,
                threaded=True,
            )
        except Exception:
            pass
