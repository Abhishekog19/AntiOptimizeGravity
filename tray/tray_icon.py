"""
tray/tray_icon.py - System tray icon for Antigravity Quota Tracker

Icon colour reflects worst-case Claude weekly quota remaining:
  Green  all accounts > 30%
  Amber  at least one <= 30%
  Red    all accounts <= 10% (or no data)

Left-click  -> activates default menu item = "Open Dashboard"
Right-click -> full menu: Open Dashboard | Capture Now | Run Diagnostics | Quit

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
import tempfile
import datetime
from pathlib import Path
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
    """
    Colour logic (worst-case across all accounts):
      Green  — all accounts > 30% weekly remaining
      Amber  — at least one account <= 30% (getting low)
      Red    — at least one account <= 10% (critically low)
      Grey   — no data yet
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
        return _COLOR_RED    # at least one account critically low
    if min_pct <= 30:
        return _COLOR_AMBER  # at least one account running low
    return _COLOR_GREEN      # all accounts healthy


# ── Multi-monitor safe window position ────────────────────────────────────────

def _safe_window_position() -> tuple:
    """
    Return (x, y) coordinates guaranteed to be on the primary monitor.
    Uses tkinter (available via Pillow's dependency chain) to query screen
    geometry.  Falls back to (100, 100) if tkinter is unavailable.

    This prevents the dashboard window from spawning off-screen on a
    disconnected second monitor — a common bug class for window-position code.
    """
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()                   # invisible probe window
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        root.destroy()
        # Place window 100px from top-left, clamped to screen bounds
        x = min(100, max(0, sw - 480))
        y = min(100, max(0, sh - 640))
        return x, y
    except Exception:
        return 100, 100


# ── Dashboard window opener ───────────────────────────────────────────────────

def _open_dashboard_window(url: str = _DASHBOARD_URL) -> None:
    """
    Spawn webview_launcher.py as a fresh subprocess so it gets its own
    main thread (required by WebView2/WKWebView). Falls back to the default
    browser if anything goes wrong.

    Passes explicit --x / --y coordinates derived from _safe_window_position()
    so the window always opens on the primary monitor (multi-monitor guard).
    """
    x, y = _safe_window_position()
    try:
        if getattr(sys, "frozen", False):
            # PyInstaller .exe — invoke self with special flag
            proc = subprocess.Popen([
                sys.executable, "--webview-launcher", url,
                "--x", str(x), "--y", str(y),
            ])
        else:
            proc = subprocess.Popen([
                sys.executable, _LAUNCHER_SCRIPT, url,
                "--x", str(x), "--y", str(y),
            ])

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


# ── Diagnostic mode ───────────────────────────────────────────────────────────

# Strings the parser expects to find in Settings > Models.
# Checked in order; report lists which are present vs missing.
_DIAGNOSTIC_STRINGS = [
    ("Claude and GPT models",  "Section header — top-level quota section"),
    ("Gemini Models",          "Section header — Gemini quota section"),
    ("Weekly Limit",           "Label inside each quota section"),
    ("Five Hour Limit",        "Label inside each quota section"),
    ("Refresh",                "Button: triggers a quota server-side refresh"),
    ("Sign out of",            "Text present in the sign-out confirmation dialog"),
]


def _run_diagnostics() -> None:
    """
    Connect to CDP port 9222, dump all page targets, check for expected UI
    strings in Settings > Models, and write a human-readable report file.
    Opens the report in Notepad (Windows) or TextEdit (Mac) for easy copying.

    Intended for reporting UI-drift bugs: if Antigravity changes its Settings
    panel text, this report shows exactly which strings are now missing.
    """
    ts      = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    lines   = []

    def _add(s: str = "") -> None:
        lines.append(s)

    _add("Antigravity Quota Tracker — Diagnostic Report")
    _add(f"Generated: {datetime.datetime.now().isoformat()}")
    _add("=" * 60)
    _add()

    # ── Import CDP helpers from notifier ──────────────────────────────────────
    try:
        _root = Path(__file__).parent.parent
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        if str(_root / "notifier") not in sys.path:
            sys.path.insert(0, str(_root / "notifier"))
        from notifier import (
            check_cdp_port,
            _get_all_page_targets,
            _is_settings_panel,
            find_settings_target,
            cdp_evaluate,
            CDP_PORT,
        )
    except ImportError as exc:
        _add(f"ERROR: Could not import notifier module: {exc}")
        _add("Make sure you are running from the project root directory.")
        _write_report(lines, ts)
        return

    # ── Port check ────────────────────────────────────────────────────────────
    _add(f"CDP Port: {CDP_PORT}")
    port_status = check_cdp_port(CDP_PORT)
    _add(f"Port status: {port_status.upper()}")
    if port_status == "not_open":
        _add("  → Antigravity is not running with --remote-debugging-port flag.")
        _add("    Launch via the debug shortcut and retry.")
        _write_report(lines, ts)
        return
    if port_status == "conflict":
        _add(f"  → Port {CDP_PORT} is in use by a NON-CDP service.")
        _add("    Stop the conflicting service or change CDP_PORT in notifier/.env.")
        _write_report(lines, ts)
        return
    _add("  → Valid CDP response confirmed.")
    _add()

    # ── Page targets ──────────────────────────────────────────────────────────
    pages = _get_all_page_targets(CDP_PORT)
    _add(f"Total CDP page targets: {len(pages)}")
    _add()

    settings_pages = [t for t in pages if _is_settings_panel(t)]
    main_pages     = [t for t in pages if not _is_settings_panel(t)]

    _add(f"Settings panel targets ({len(settings_pages)}):")
    for t in settings_pages:
        _add(f"  id={t['id'][:8]}  title={t.get('title', '')!r}")
        _add(f"  url={t.get('url', '')[:80]}")
    _add()

    _add(f"Main editor targets ({len(main_pages)}):")
    for t in main_pages:
        _add(f"  id={t['id'][:8]}  title={t.get('title', '')!r}")
        _add(f"  url={t.get('url', '')[:80]}")
    _add()

    # ── Settings > Models string check ────────────────────────────────────────
    settings_t = find_settings_target(CDP_PORT)
    if not settings_t:
        _add("Settings panel target NOT FOUND.")
        _add("  → Open Antigravity → Settings → Models, then retry.")
        _write_report(lines, ts)
        return

    # Navigate to Models and read innerText
    cdp_evaluate(settings_t, "history.pushState({}, '', '/?settingsScreen=Models')")
    time.sleep(1.5)
    inner_text = cdp_evaluate(settings_t, "document.documentElement.innerText") or ""

    _add("Settings > Models — Expected string presence check:")
    _add("-" * 50)
    all_ok = True
    for needle, description in _DIAGNOSTIC_STRINGS:
        found = needle in inner_text
        status = "FOUND   " if found else "MISSING "
        if not found:
            all_ok = False
        _add(f"  [{status}] {needle!r}")
        _add(f"            ({description})")

    _add()
    if all_ok:
        _add("All expected strings FOUND. Parser should work correctly.")
    else:
        _add("One or more strings MISSING.")
        _add("This indicates a UI change in Antigravity that broke the parser.")
        _add("Please copy this report and open a GitHub issue.")

    _add()
    _add("=" * 60)
    _add("Raw innerText from Settings > Models (first 3000 chars):")
    _add("-" * 60)
    _add(inner_text[:3000])
    _add("..." if len(inner_text) > 3000 else "")

    _write_report(lines, ts)


def _write_report(lines: list, ts: str) -> None:
    """Write the diagnostic report to a temp file and open it."""
    content = "\n".join(lines)
    try:
        # Write to temp directory so it's always writable
        tmp_path = Path(tempfile.gettempdir()) / f"ag_diagnostics_{ts}.txt"
        tmp_path.write_text(content, encoding="utf-8")
        log.info(f"Diagnostic report written to: {tmp_path}")

        # Open in default text editor
        if sys.platform == "win32":
            subprocess.Popen(["notepad.exe", str(tmp_path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-t", str(tmp_path)])
        else:
            subprocess.Popen(["xdg-open", str(tmp_path)])

        _show_toast(
            f"Diagnostic report saved to:\n{tmp_path.name}\nOpened in text editor.",
            "Diagnostics Complete",
        )
    except Exception as exc:
        log.error(f"Could not write/open diagnostic report: {exc}")
        _show_toast(f"Diagnostics error: {exc}", "Diagnostics Failed")


def _show_toast(message: str, title: str = "Antigravity Quota Tracker") -> None:
    """Fire a best-effort toast notification from the tray module."""
    try:
        from win10toast import ToastNotifier
        ToastNotifier().show_toast(title, message, duration=8, threaded=True)
    except Exception:
        try:
            from plyer import notification
            notification.notify(title=title, message=message, timeout=8)
        except Exception:
            pass


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
            Item("Run Diagnostics", self._on_run_diagnostics),
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
        # Compute min pct for tooltip
        pcts = [
            a["latest"]["claude_weekly_pct"]
            for a in accounts
            if a.get("latest") and a["latest"].get("claude_weekly_pct") is not None
        ]
        min_pct = min(pcts) if pcts else None

        if color == self._current_color:
            return
        self._current_color = color

        if min_pct is not None:
            label_map = {
                _COLOR_GREEN: f"All accounts healthy (lowest: {min_pct:.0f}%)",
                _COLOR_AMBER: f"Some accounts running low (lowest: {min_pct:.0f}%)",
                _COLOR_RED:   f"Account critically low! (lowest: {min_pct:.0f}%)",
                _COLOR_GREY:  "No quota data yet",
            }
        else:
            label_map = {
                _COLOR_GREEN: "All accounts healthy",
                _COLOR_AMBER: "Some accounts running low",
                _COLOR_RED:   "Account critically low!",
                _COLOR_GREY:  "No quota data yet",
            }
        try:
            self._icon.icon  = _make_icon_image(color)
            self._icon.title = f"Quota Tracker \u2014 {label_map.get(color, '')}"
        except Exception as exc:
            log.debug("Icon update failed: %s", exc)

    def notify(self, title: str, message: str) -> None:
        _show_toast(message, title)

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

    def _on_run_diagnostics(self, icon=None, item=None) -> None:
        """Launch diagnostic report in a daemon thread."""
        _show_toast(
            "Connecting to Antigravity via CDP...\nReport will open when ready.",
            "Running Diagnostics",
        )
        threading.Thread(
            target=_run_diagnostics,
            daemon=True,
            name="Diagnostics",
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
        self._check_antigravity_running()
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
            time.sleep(5)   # 5 s — responsive to captures without burning CPU

    def _notify_startup(self) -> None:
        _show_toast(
            "Running in system tray. Left-click the dot to open dashboard.",
            "Antigravity Quota Tracker",
        )

    def _check_antigravity_running(self) -> None:
        """
        One-time startup check: warn if Antigravity is not running with the
        CDP debug flag.  Uses check_cdp_port() — which validates the JSON
        structure — rather than a simple port probe.

        Does NOT block startup. Runs after a short delay so Flask + watcher
        have time to initialize before we bother the user with a notification.
        """
        time.sleep(3.0)   # let watcher start and do its own detection first
        try:
            _root = Path(__file__).parent.parent
            if str(_root / "notifier") not in sys.path:
                sys.path.insert(0, str(_root / "notifier"))
            from notifier import check_cdp_port, CDP_PORT as _port
            status = check_cdp_port(_port)
            if status == "not_open":
                log.info(
                    "Startup check: Antigravity not found on CDP port %s "
                    "(may not be launched yet — this is normal on first start).",
                    _port,
                )
                _show_toast(
                    "Antigravity not detected yet.\n"
                    "Make sure you launch it via the debug shortcut\n"
                    "(run scripts/setup-windows.ps1 once if not done).",
                    "Antigravity Quota Tracker — Setup Required",
                )
            elif status == "conflict":
                log.warning(
                    "Startup check: port %s is occupied by a non-CDP service.",
                    _port,
                )
                _show_toast(
                    f"Port {_port} is occupied by another service.\n"
                    "Change CDP_PORT in notifier/.env to resolve the conflict.",
                    "Antigravity Quota Tracker — Port Conflict",
                )
            # status == "ok": Antigravity is running with the flag — no notification needed.
        except Exception as exc:
            log.debug("Startup CDP check failed: %s", exc)

