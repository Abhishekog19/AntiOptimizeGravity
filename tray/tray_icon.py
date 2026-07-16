"""
tray/tray_icon.py — System tray icon for Antigravity Quota Tracker

Uses pystray + Pillow to draw a coloured circle icon whose colour reflects
the worst-case quota status across all tracked accounts:

  Green  All accounts have claude_weekly_pct > 30
  Amber  At least one account <= 30%
  Red    All accounts <= 10%  (or no accounts at all)

Right-click menu:
  Open Dashboard  -> opens http://localhost:4300 in browser
  Capture Now     -> fires a manual capture
  ──────────────
  Quit            -> graceful shutdown

Left-click -> opens / hides the QuotaPopup window.

NOTE for Windows users: if the icon is not visible in the taskbar,
look for it in the overflow area (click the ^ arrow near the clock).
Right-click the icon there and select "Show icon and notifications" to pin it.
"""

from __future__ import annotations
import sys
import os
import threading
import time
import webbrowser
import logging
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
_COLOR_GREEN  = (74,  222, 128, 255)   # #4ade80
_COLOR_AMBER  = (251, 191,  36, 255)   # #fbbf24
_COLOR_RED    = (248, 113, 113, 255)   # #f87171
_COLOR_GREY   = (90,  90,   90, 255)   # starting / no data
_ICON_SIZE    = 64


def _make_icon_image(color: tuple) -> "Image.Image":
    """Draw a coloured filled circle on a transparent background."""
    img  = Image.new("RGBA", (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad  = 8
    draw.ellipse(
        [pad, pad, _ICON_SIZE - pad, _ICON_SIZE - pad],
        fill=color,
    )
    # Small white ring to make it pop on both light and dark taskbars
    draw.ellipse(
        [pad - 2, pad - 2, _ICON_SIZE - pad + 2, _ICON_SIZE - pad + 2],
        outline=(255, 255, 255, 60),
        width=1,
    )
    return img


def _compute_icon_color(accounts: list) -> tuple:
    """
    Determine tray icon colour from cached account data.
    Values are % REMAINING — low remaining = bad.
    """
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


def _send_startup_toast() -> None:
    """Show a Windows toast notifying user that the app is in the tray."""
    try:
        from win10toast import ToastNotifier
        n = ToastNotifier()
        n.show_toast(
            "Antigravity Quota Tracker",
            "Running in the system tray.\n"
            "Look for the coloured dot near the clock (^ overflow if hidden).\n"
            "Left-click = status popup | Right-click = menu",
            duration=8,
            threaded=True,
        )
    except Exception:
        pass  # toast is non-critical


# ── TrayIcon class ────────────────────────────────────────────────────────────

class TrayIcon:
    """
    Wraps a pystray.Icon instance.

    Call run() to start (blocks the main thread on Windows — required).
    """

    def __init__(
        self,
        popup_toggle_fn: Optional[Callable] = None,
        fire_capture_fn: Optional[Callable] = None,
        quit_fn:         Optional[Callable] = None,
    ) -> None:
        self._popup_toggle  = popup_toggle_fn
        self._fire_capture  = fire_capture_fn
        self._quit_fn       = quit_fn
        self._icon: Optional["pystray.Icon"] = None
        self._current_color = _COLOR_GREY

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self) -> None:
        """
        Start the tray icon (blocking). Call on the main thread.
        Falls back to a plain sleep loop if pystray/Pillow are unavailable.
        """
        if not _HAS_PYSTRAY or not _HAS_PILLOW:
            log.warning(
                "pystray or Pillow not installed — tray icon unavailable. "
                "Dashboard still accessible at http://localhost:4300"
            )
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
            return

        # Build menu — the default=True item is also triggered on left-click
        # double-click on Windows (pystray behaviour)
        menu = Menu(
            Item(
                "Status Window",          # also accessible via left-click on_activate
                self._on_status_window,
                default=False,
            ),
            Item(
                "Open Dashboard",
                self._on_open_dashboard,
            ),
            Item(
                "Capture Now",
                self._on_capture_now,
            ),
            Menu.SEPARATOR,
            Item(
                "Quit Antigravity Tracker",
                self._on_quit,
            ),
        )

        initial_img = _make_icon_image(_COLOR_GREY)

        self._icon = pystray.Icon(
            name="antigravity-quota-tracker",
            icon=initial_img,
            title="Antigravity Quota Tracker — starting...",
            menu=menu,
            on_activate=self._on_left_click,   # left-click handler (set in ctor)
        )

        # Background: update icon colour + send startup toast
        threading.Thread(
            target=self._startup_sequence,
            daemon=True,
            name="TrayStartup",
        ).start()

        log.info("Tray icon starting... (look for coloured dot near the clock, or ^ overflow)")
        self._icon.run()

    def update_icon(self, accounts: list) -> None:
        """Update the tray icon colour based on current account data."""
        if not self._icon:
            return
        color = _compute_icon_color(accounts)
        if color == self._current_color:
            return
        self._current_color = color
        status = {
            _COLOR_GREEN: "All accounts healthy",
            _COLOR_AMBER: "Some accounts running low",
            _COLOR_RED:   "Accounts near-empty!",
            _COLOR_GREY:  "No account data yet",
        }.get(color, "")
        try:
            self._icon.icon  = _make_icon_image(color)
            self._icon.title = f"Antigravity Quota Tracker — {status}"
        except Exception as exc:
            log.debug(f"Icon update error: {exc}")

    def stop(self) -> None:
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass

    # ── Menu handlers ─────────────────────────────────────────────────────────

    def _on_left_click(self, icon, item=None) -> None:
        """Left-click handler - toggle the popup."""
        if self._popup_toggle:
            threading.Thread(
                target=self._popup_toggle,
                daemon=True,
                name="PopupToggle",
            ).start()

    def _on_status_window(self, icon, item) -> None:
        """Right-click 'Status Window' menu item."""
        if self._popup_toggle:
            threading.Thread(
                target=self._popup_toggle,
                daemon=True,
                name="PopupToggleMenu",
            ).start()

    def _on_open_dashboard(self, icon, item) -> None:
        webbrowser.open("http://localhost:4300")

    def _on_capture_now(self, icon, item) -> None:
        if self._fire_capture:
            threading.Thread(
                target=self._fire_capture,
                daemon=True,
                name="TrayCapture",
            ).start()

    def _on_quit(self, icon, item) -> None:
        icon.stop()
        if self._quit_fn:
            self._quit_fn()

    # ── Background sequences ─────────────────────────────────────────────────

    def _startup_sequence(self) -> None:
        """Run after the icon starts: send toast + begin colour-update loop."""
        time.sleep(1.5)  # wait for icon to fully appear

        # Toast
        threading.Thread(target=_send_startup_toast, daemon=True).start()

        # Update icon immediately with current account data
        from state import app_state
        self.update_icon(app_state.get_accounts())

        # Colour update loop (every 30 s)
        self._icon_update_loop()

    def _icon_update_loop(self) -> None:
        from state import app_state
        while True:
            try:
                self.update_icon(app_state.get_accounts())
            except Exception as exc:
                log.debug(f"Icon update error: {exc}")
            time.sleep(30)
