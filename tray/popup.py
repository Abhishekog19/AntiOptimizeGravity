"""
tray/popup.py - Minimal dark-themed status popup for Antigravity Quota Tracker

Opens on tray left-click or "Status Window" menu item.
Closes ONLY via the X button or Escape key (NOT on focus-out, which caused
the window to immediately self-close when the tray overflow kept focus).

Layout
------
  +-----------------------------------+
  |  ANTIGRAVITY  - quota tracker  [x] |
  +-----------------------------------+
  |  [star] USE NEXT                  |
  |  user@email.com                   |
  |  Weekly  [========--]  82%        |
  |  5-hour  [=====-----]  48%        |
  |  resets in  6d 2h                 |
  +-----------------------------------+
  |  user2@email.com                  |
  |  ...                              |
  +-----------------------------------+
  |  -- ACTIVITY --                   |
  |  20:15  [ok]  Captured (launch)   |
  |  20:14  [..] Capturing...         |
  +-----------------------------------+
  |  [Capture Now]  [Open Dashboard]  |
  |  Last captured: 3 min ago         |
  +-----------------------------------+
"""

from __future__ import annotations
import tkinter as tk
import threading
import time
import webbrowser
from typing import Optional, Callable

# ── Design tokens ─────────────────────────────────────────────────────────────
BG          = "#0c0c0c"
BG_CARD     = "#111111"
BG_HEADER   = "#0a0a0a"
BORDER      = "#222222"
TEXT_PRI    = "#f0f0f0"
TEXT_SEC    = "#6b6b6b"
TEXT_DIM    = "#3a3a3a"
ACCENT      = "#ffffff"
BAR_TRACK   = "#1e1e1e"
BAR_FILL    = "#ffffff"
BAR_WARN    = "#c8a84b"
BAR_DANGER  = "#c84b4b"
BADGE_BG    = "#ffffff"
BADGE_FG    = "#000000"
STATUS_OK   = "#4ade80"
STATUS_WARN = "#fbbf24"
STATUS_ERR  = "#f87171"
STATUS_INFO = "#6b6b6b"

FONT_MONO   = ("Courier New", 9)
FONT_SMALL  = ("Segoe UI", 9)
FONT_MED    = ("Segoe UI", 10)
FONT_SEMIB  = ("Segoe UI Semibold", 10)
FONT_TITLE  = ("Segoe UI Light", 11)
FONT_BADGE  = ("Segoe UI Semibold", 7)
FONT_LABEL  = ("Segoe UI", 8)

WINDOW_W    = 310
MAX_EVENTS  = 6


# ── Helpers ────────────────────────────────────────────────────────────────────

def _time_ago(ts: Optional[float]) -> str:
    if ts is None:
        return "never"
    secs = int(time.time() - ts)
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    return f"{secs // 3600}h ago"


def _time_until(iso: Optional[str]) -> str:
    if not iso:
        return ""
    try:
        import datetime
        dt  = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        now = datetime.datetime.now(datetime.timezone.utc)
        secs = (dt - now).total_seconds()
        if secs <= 0:
            return "now"
        total_mins = int(secs / 60)
        mins  = total_mins % 60
        hrs   = (total_mins // 60) % 24
        days  = total_mins // 1440
        if days > 0 and hrs > 0:
            return f"{days}d {hrs}h"
        if days > 0:
            return f"{days}d"
        if hrs > 0 and mins > 0:
            return f"{hrs}h {mins}m"
        if hrs > 0:
            return f"{hrs}h"
        return f"{mins}m"
    except Exception:
        return ""


def _bar_color(pct: Optional[float]) -> str:
    if pct is None:
        return BAR_FILL
    if pct <= 10:
        return BAR_DANGER
    if pct <= 30:
        return BAR_WARN
    return BAR_FILL


def _pct_color(pct: Optional[float]) -> str:
    if pct is None:
        return TEXT_DIM
    if pct <= 10:
        return STATUS_ERR
    if pct <= 30:
        return STATUS_WARN
    return TEXT_PRI


def _event_color(level: str) -> str:
    return {
        "ok":       STATUS_OK,
        "error":    STATUS_ERR,
        "warn":     STATUS_WARN,
        "progress": TEXT_SEC,
        "info":     TEXT_SEC,
    }.get(level, TEXT_SEC)


def _event_dot(level: str) -> str:
    return {
        "ok":       "OK",
        "error":    "!!",
        "warn":     ">>",
        "progress": "..",
        "info":     "--",
    }.get(level, "--")


# ── Progress bar widget ────────────────────────────────────────────────────────

class _ProgressBar(tk.Canvas):
    HEIGHT = 3

    def __init__(self, parent, width: int = 180, **kwargs):
        super().__init__(
            parent,
            width=width,
            height=self.HEIGHT,
            bg=BG_CARD,
            highlightthickness=0,
            **kwargs,
        )
        self._bar_w = width
        self.create_rectangle(0, 0, width, self.HEIGHT, fill=BAR_TRACK, outline="")
        self._fill_rect = self.create_rectangle(0, 0, 0, self.HEIGHT, fill=BAR_FILL, outline="")

    def set(self, pct: Optional[float]) -> None:
        v     = max(0.0, min(100.0, pct or 0.0))
        color = _bar_color(pct)
        fw    = int(self._bar_w * v / 100)
        self.itemconfig(self._fill_rect, fill=color)
        self.coords(self._fill_rect, 0, 0, fw, self.HEIGHT)


# ── Main popup window ──────────────────────────────────────────────────────────

class QuotaPopup:
    """
    Frameless dark popup window.

    Closes ONLY via the X button or Escape key - NOT on focus-out.
    Auto-refreshes every 2 seconds.
    """

    def __init__(self, fire_capture_fn: Optional[Callable] = None) -> None:
        self._fire_capture = fire_capture_fn
        self._root: Optional[tk.Tk] = None
        self._win:  Optional[tk.Toplevel] = None
        self._visible = False
        self._after_id = None
        self._lock = threading.Lock()

        # Start the Tk event loop thread immediately (once, at init)
        self._tk_ready = threading.Event()
        t = threading.Thread(target=self._run_tk, daemon=True, name="TkPopup")
        t.start()
        # Wait max 2s for Tk to boot
        self._tk_ready.wait(timeout=2.0)

    def _run_tk(self) -> None:
        """Run tkinter main loop in its own thread."""
        self._root = tk.Tk()
        self._root.withdraw()          # hidden root window
        self._root.title("")
        self._tk_ready.set()
        self._root.mainloop()

    # ── Public API ─────────────────────────────────────────────────────────────

    def show(self) -> None:
        """Show the popup (thread-safe)."""
        with self._lock:
            if self._visible:
                # Already shown - lift it to front
                if self._root and self._win:
                    self._root.after(0, lambda: self._win.lift() if self._win else None)
                return
            if self._root:
                self._root.after(0, self._open_window)

    def hide(self) -> None:
        """Close the popup (thread-safe)."""
        with self._lock:
            if not self._visible:
                return
            if self._root:
                self._root.after(0, self._close_window)

    def toggle(self) -> None:
        """Toggle show/hide (called from tray left-click)."""
        if self._visible:
            self.hide()
        else:
            self.show()

    # ── Window construction ────────────────────────────────────────────────────

    def _open_window(self) -> None:
        """Called on the Tk thread via after()."""
        if self._visible:
            return

        win = tk.Toplevel(self._root)
        self._win = win
        win.overrideredirect(True)          # frameless
        win.attributes("-topmost", True)    # always on top
        win.configure(bg=BG)
        win.resizable(False, False)

        self._build_ui(win)
        self._position_window(win)

        self._visible = True
        win.deiconify()
        win.lift()
        win.focus_force()

        # Close on Escape only (NOT on FocusOut - that caused instant-close)
        win.bind("<Escape>", lambda e: self.hide())

        self._schedule_refresh()

    def _close_window(self) -> None:
        """Called on the Tk thread."""
        if self._after_id:
            try:
                self._root.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        if self._win:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None
        self._visible = False

    def _position_window(self, win: tk.Toplevel) -> None:
        win.update_idletasks()
        w  = win.winfo_reqwidth()
        h  = win.winfo_reqheight()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x  = sw - w - 12
        y  = sh - h - 52       # above taskbar
        x  = max(0, min(x, sw - w))
        y  = max(0, min(y, sh - h - 10))
        win.geometry(f"{w}x{h}+{x}+{y}")

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self, win: tk.Toplevel) -> None:
        # ── Header ────────────────────────────────────────────────────────────
        header = tk.Frame(win, bg=BG_HEADER, pady=9)
        header.pack(fill="x")

        tk.Label(
            header, text="ANTIGRAVITY",
            font=("Segoe UI Semibold", 11),
            fg=TEXT_PRI, bg=BG_HEADER,
        ).pack(side="left", padx=(14, 0))

        tk.Label(
            header, text="  quota tracker",
            font=FONT_TITLE,
            fg=TEXT_SEC, bg=BG_HEADER,
        ).pack(side="left")

        close_lbl = tk.Label(
            header, text="  x  ",
            font=("Segoe UI", 12),
            fg=TEXT_DIM, bg=BG_HEADER,
            cursor="hand2",
        )
        close_lbl.pack(side="right", padx=(0, 4))
        close_lbl.bind("<Button-1>", lambda e: self.hide())
        close_lbl.bind("<Enter>",    lambda e: close_lbl.configure(fg=TEXT_PRI))
        close_lbl.bind("<Leave>",    lambda e: close_lbl.configure(fg=TEXT_DIM))

        # ── Separator ─────────────────────────────────────────────────────────
        tk.Frame(win, bg=BORDER, height=1).pack(fill="x")

        # ── Accounts area ──────────────────────────────────────────────────────
        self._accounts_frame = tk.Frame(win, bg=BG)
        self._accounts_frame.pack(fill="x")

        # ── Separator ─────────────────────────────────────────────────────────
        tk.Frame(win, bg=BORDER, height=1).pack(fill="x")

        # ── Activity log header ────────────────────────────────────────────────
        log_hdr = tk.Frame(win, bg=BG, pady=5)
        log_hdr.pack(fill="x", padx=14)
        tk.Label(
            log_hdr, text="ACTIVITY",
            font=("Courier New", 7),
            fg=TEXT_DIM, bg=BG,
        ).pack(side="left")

        # ── Activity log rows ──────────────────────────────────────────────────
        self._log_frame = tk.Frame(win, bg=BG)
        self._log_frame.pack(fill="x", padx=14, pady=(0, 8))

        # ── Separator ─────────────────────────────────────────────────────────
        tk.Frame(win, bg=BORDER, height=1).pack(fill="x")

        # ── Action buttons ─────────────────────────────────────────────────────
        btn_row = tk.Frame(win, bg=BG, pady=9)
        btn_row.pack(fill="x", padx=14)

        self._capture_btn = self._flat_btn(
            btn_row, "  Capture Now  ",
            self._on_capture_now, primary=True,
        )
        self._capture_btn.pack(side="left")

        self._flat_btn(
            btn_row, "  Dashboard  ",
            lambda: webbrowser.open("http://localhost:4300"), primary=False,
        ).pack(side="left", padx=(8, 0))

        # ── Footer ────────────────────────────────────────────────────────────
        tk.Frame(win, bg=BORDER, height=1).pack(fill="x")
        footer = tk.Frame(win, bg=BG_HEADER, pady=6)
        footer.pack(fill="x")
        self._last_cap_lbl = tk.Label(
            footer, text="Last captured: --",
            font=FONT_LABEL, fg=TEXT_DIM, bg=BG_HEADER,
        )
        self._last_cap_lbl.pack(side="left", padx=14)

        # Initial content render
        self._render_content()

    def _flat_btn(self, parent, text: str, command, primary: bool = False) -> tk.Label:
        bg_n = ACCENT if primary else BG_CARD
        bg_h = "#d8d8d8" if primary else "#1e1e1e"
        btn = tk.Label(
            parent, text=text,
            font=FONT_SMALL,
            fg=BADGE_FG if primary else TEXT_SEC,
            bg=bg_n, padx=8, pady=5,
            cursor="hand2",
        )
        btn.bind("<Button-1>", lambda e: command())
        btn.bind("<Enter>",    lambda e: btn.configure(bg=bg_h))
        btn.bind("<Leave>",    lambda e: btn.configure(bg=bg_n))
        return btn

    # ── Content rendering ──────────────────────────────────────────────────────

    def _render_content(self) -> None:
        """Refresh accounts + activity. Called on Tk thread every 2s."""
        from state import app_state

        # -- Accounts --
        for w in self._accounts_frame.winfo_children():
            w.destroy()

        accounts = app_state.get_accounts()
        if not accounts:
            tk.Label(
                self._accounts_frame,
                text="No accounts captured yet.",
                font=FONT_SMALL, fg=TEXT_DIM, bg=BG,
            ).pack(padx=14, pady=10)
        else:
            for i, acct in enumerate(accounts[:5]):
                self._build_account_card(acct, is_top=(i == 0))
                if i < min(len(accounts), 5) - 1:
                    tk.Frame(self._accounts_frame, bg=BORDER, height=1).pack(fill="x", padx=14)

        # -- Activity log --
        for w in self._log_frame.winfo_children():
            w.destroy()

        events = app_state.get_events()[:MAX_EVENTS]
        if not events:
            tk.Label(
                self._log_frame,
                text="Waiting for first capture...",
                font=FONT_MONO, fg=TEXT_DIM, bg=BG,
            ).pack(anchor="w")
        else:
            for ts, level, msg in events:
                self._build_event_row(ts, level, msg)

        # -- Last captured --
        lca = app_state.last_capture_at
        self._last_cap_lbl.configure(text=f"Last captured: {_time_ago(lca)}")

        # -- Capture button state --
        if app_state.is_capturing:
            self._capture_btn.configure(text="  Capturing...  ", bg="#1a1a1a", fg=TEXT_SEC)
        else:
            self._capture_btn.configure(text="  Capture Now  ", bg=ACCENT, fg=BADGE_FG)

    def _build_account_card(self, acct: dict, is_top: bool) -> None:
        card_bg = BG_CARD if is_top else BG
        card = tk.Frame(self._accounts_frame, bg=card_bg, pady=10, padx=14)
        card.pack(fill="x")

        # Name row
        name_row = tk.Frame(card, bg=card_bg)
        name_row.pack(fill="x", pady=(0, 3))

        if is_top:
            tk.Label(
                name_row, text=" USE NEXT ",
                font=FONT_BADGE, fg=BADGE_FG, bg=BADGE_BG, padx=2,
            ).pack(side="left", padx=(0, 6))

        tk.Label(
            name_row,
            text=acct.get("displayName", acct.get("id", "—")),
            font=FONT_SEMIB if is_top else FONT_MED,
            fg=TEXT_PRI, bg=card_bg, anchor="w",
        ).pack(side="left")

        l = acct.get("latest") or {}
        self._bar_row(card, card_bg, "Weekly", l.get("claude_weekly_pct"))
        self._bar_row(card, card_bg, "5-hour", l.get("claude_fivehour_pct"))

        reset_in = _time_until(l.get("claude_weekly_reset_at"))
        if reset_in:
            tk.Label(
                card, text=f"resets in  {reset_in}",
                font=FONT_LABEL, fg=TEXT_DIM, bg=card_bg, anchor="w",
            ).pack(fill="x", pady=(3, 0))

    def _bar_row(self, parent, bg: str, label: str, pct: Optional[float]) -> None:
        row = tk.Frame(parent, bg=bg, pady=2)
        row.pack(fill="x")
        tk.Label(row, text=label, font=FONT_LABEL, fg=TEXT_SEC, bg=bg, width=7, anchor="w").pack(side="left")
        bar = _ProgressBar(row, width=158, bg=bg)
        bar.pack(side="left", padx=(4, 8))
        bar.set(pct)
        pct_str = f"{round(pct)}%" if pct is not None else "--"
        tk.Label(row, text=pct_str, font=FONT_MONO, fg=_pct_color(pct), bg=bg, width=5, anchor="e").pack(side="left")

    def _build_event_row(self, ts: float, level: str, msg: str) -> None:
        row = tk.Frame(self._log_frame, bg=BG)
        row.pack(fill="x", pady=1)

        ts_str = time.strftime("%H:%M", time.localtime(ts))
        tk.Label(row, text=ts_str, font=FONT_MONO, fg=TEXT_DIM, bg=BG, width=5, anchor="w").pack(side="left")
        tk.Label(row, text=f"[{_event_dot(level)}]", font=FONT_MONO, fg=_event_color(level), bg=BG, width=5).pack(side="left")

        display_msg = msg[:40] + "..." if len(msg) > 40 else msg
        fg = TEXT_SEC if level in ("info", "progress") else TEXT_PRI
        tk.Label(row, text=display_msg, font=FONT_LABEL, fg=fg, bg=BG, anchor="w").pack(side="left", fill="x")

    # ── Auto-refresh ───────────────────────────────────────────────────────────

    def _schedule_refresh(self) -> None:
        if self._visible and self._win:
            try:
                self._render_content()
                self._after_id = self._root.after(2000, self._schedule_refresh)
            except Exception:
                pass

    # ── Capture button ─────────────────────────────────────────────────────────

    def _on_capture_now(self) -> None:
        from state import app_state
        if app_state.is_capturing or not self._fire_capture:
            return
        threading.Thread(target=self._fire_capture, daemon=True, name="ManualCapture").start()
