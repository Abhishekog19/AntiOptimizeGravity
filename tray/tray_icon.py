"""
tray/tray_icon.py — System tray icon for Antigravity Quota Tracker

Uses pystray + Pillow to draw a coloured circle icon whose colour reflects
the worst-case quota status across all tracked accounts:

  🟢 Green   All accounts have claude_weekly_pct > 30
  🟡 Amber   At least one account ≤ 30%
  🔴 Red     All accounts ≤ 10%  (or no accounts at all)

Right-click menu:
  Open Dashboard  → opens http://localhost:4300 in browser
  Capture Now     → fires a manual capture
  ──────────────
  Quit            → graceful shutdown

Left-click → opens / hides the QuotaPopup window.
"""

from __future__ import annotations
import sys
import threading
import time
import webbrowser
import logging
from typing import Optional, Callable

try:
    import pystray
    from pystray import MenuItem as Item
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
_COLOR_GREY   = (80,  80,   80, 255)   # fallback / starting state
_ICON_SIZE    = 64


def _make_icon_image(color: tuple) -> "Image.Image":
    """Draw a coloured filled circle on a transparent background."""
    img  = Image.new("RGBA", (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad  = 6
    draw.ellipse(
        [pad, pad, _ICON_SIZE - pad, _ICON_SIZE - pad],
        fill=color,
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


# ── TrayIcon class ────────────────────────────────────────────────────────────

class TrayIcon:
    """
    Wraps a pystray.Icon instance.

    call run() to start blocking on the tray icon (must be on the main thread
    on Windows/macOS for pystray to work correctly).
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
        Start the tray icon (blocking).  Call on the main thread.
        Falls back to a plain sleep loop if pystray/Pillow are unavailable.
        """
        if not _HAS_PYSTRAY or not _HAS_PILLOW:
            log.warning(
                "pystray or Pillow not installed — tray icon unavailable. "
                "Dashboard still accessible at http://localhost:4300"
            )
            # Keep main thread alive without a tray icon
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
            return

        menu = pystray.Menu(
            Item("Open Dashboard",  self._on_open_dashboard, default=False),
            Item("Capture Now",     self._on_capture_now),
            pystray.Menu.SEPARATOR,
            Item("Quit",            self._on_quit),
        )

        self._icon = pystray.Icon(
            name="antigravity-quota-tracker",
            icon=_make_icon_image(_COLOR_GREY),
            title="Antigravity Quota Tracker",
            menu=menu,
        )

        # Update icon colour in background
        threading.Thread(
            target=self._icon_update_loop,
            daemon=True,
            name="TrayIconUpdater",
        ).start()

        # Left-click handler
        self._icon.on_activate = self._on_left_click

        log.info("Tray icon started")
        self._icon.run()

    def update_icon(self, accounts: list) -> None:
        """Update the tray icon colour based on current account data."""
        if not self._icon:
            return
        color = _compute_icon_color(accounts)
        if color == self._current_color:
            return
        self._current_color = color
        try:
            self._icon.icon = _make_icon_image(color)
            status = {
                _COLOR_GREEN: "All accounts healthy",
                _COLOR_AMBER: "Some accounts low",
                _COLOR_RED:   "Accounts near-empty",
                _COLOR_GREY:  "No data yet",
            }.get(color, "")
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
        if self._popup_toggle:
            threading.Thread(
                target=self._popup_toggle,
                daemon=True,
                name="PopupToggle",
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

    # ── Background icon colour update ─────────────────────────────────────────

    def _icon_update_loop(self) -> None:
        from state import app_state
        while True:
            try:
                accounts = app_state.get_accounts()
                self.update_icon(accounts)
            except Exception as exc:
                log.debug(f"Icon update loop error: {exc}")
            time.sleep(30)  # check every 30 s
