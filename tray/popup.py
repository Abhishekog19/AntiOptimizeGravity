"""
tray/popup.py — Minimal dark-themed status popup for Antigravity Quota Tracker

Opens on tray icon left-click, closes on Escape or click-outside.
Design: monochrome / near-black + white, small tracker-card aesthetic.

Layout
──────
  ┌─────────────────────────────────┐
  │  ANTIGRAVITY  ·  quota tracker  │  ← header
  ├─────────────────────────────────┤
  │  ★ USE NEXT                     │  ← badge (top-ranked only)
  │  user@email.com                 │
  │  Weekly  [████████░░]  82%      │
  │  5-hour  [█████░░░░░]  48%      │
  │  resets in  6d 2h               │
  ├─────────────────────────────────┤
  │  user2@email.com                │
  │  …                              │
  ├─────────────────────────────────┤
  │  ── Activity ──────────────────  │
  │  ● 20:15 Captured (launch)      │
  │  ○ 20:14 Capturing…             │
  ├─────────────────────────────────┤
  │  [Capture Now]  [Open Dashboard]│
  │  Last captured: 3 min ago       │
  └─────────────────────────────────┘
"""

from __future__ import annotations
import tkinter as tk
import threading
import time
import webbrowser
from typing import Optional, Callable

# ── Design tokens ─────────────────────────────────────────────────────────────
BG          = "#0c0c0c"    # near-black background
BG_CARD     = "#111111"    # card background
BG_HEADER   = "#0a0a0a"    # header strip
BORDER      = "#1e1e1e"    # subtle border
TEXT_PRI    = "#f0f0f0"    # primary text
TEXT_SEC    = "#6b6b6b"    # secondary / muted text
TEXT_DIM    = "#3a3a3a"    # very dim (separators)
ACCENT      = "#ffffff"    # white accent
BAR_TRACK   = "#1a1a1a"    # progress bar track
BAR_FILL    = "#ffffff"    # progress bar fill (white)
BAR_WARN    = "#c8a84b"    # amber fill when ≤30%
BAR_DANGER  = "#c84b4b"    # red fill when ≤10%
BADGE_BG    = "#ffffff"
BADGE_FG    = "#000000"
STATUS_OK   = "#4ade80"    # green
STATUS_WARN = "#fbbf24"    # amber
STATUS_ERR  = "#f87171"    # red
STATUS_INFO = "#6b6b6b"    # grey

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
        return "—"
    try:
        import datetime
        dt  = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        now = datetime.datetime.now(datetime.timezone.utc)
        ms  = (dt - now).total_seconds() * 1000
        if ms <= 0:
            return "now"
        total_mins = int(ms / 60000)
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
        return "—"


def _bar_color(pct: Optional[float]) -> str:
    if pct is None:
        return BAR_FILL
    if pct <= 10:
        return BAR_DANGER
    if pct <= 30:
        return BAR_WARN
    return BAR_FILL


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
        "ok":       "●",
        "error":    "✕",
        "warn":     "▲",
        "progress": "○",
        "info":     "·",
    }.get(level, "·")


# ── Progress bar widget ────────────────────────────────────────────────────────

class ProgressBar(tk.Canvas):
    """A minimal horizontal progress bar rendered in a Canvas."""

    HEIGHT = 3
    RADIUS = 2

    def __init__(self, parent, width: int = 200, **kwargs):
        super().__init__(
            parent,
            width=width,
            height=self.HEIGHT,
            bg=BG_CARD,
            highlightthickness=0,
            **kwargs,
        )
        self._bar_width = width
        self._track = self.create_rectangle(
            0, 0, width, self.HEIGHT,
            fill=BAR_TRACK, outline=""
        )
        self._fill = self.create_rectangle(
            0, 0, 0, self.HEIGHT,
            fill=BAR_FILL, outline=""
        )

    def set(self, pct: Optional[float]) -> None:
        v      = max(0, min(100, pct or 0))
        color  = _bar_color(pct)
        fill_w = int(self._bar_width * v / 100)
        self.itemconfig(self._fill, fill=color)
        self.coords(self._fill, 0, 0, fill_w, self.HEIGHT)


# ── Main popup window ──────────────────────────────────────────────────────────

class QuotaPopup:
    """
    Frameless dark popup window.

    Usage
    ─────
      popup = QuotaPopup(
          fire_capture_fn=lambda: notifier.fire_capture("manual_tray"),
      )
      popup.show()   # call from tray left-click (in tray thread)
      popup.hide()   # called automatically on Escape / click-outside
    """

    def __init__(self, fire_capture_fn: Optional[Callable] = None) -> None:
        self._fire_capture = fire_capture_fn
        self._root: Optional[tk.Tk] = None
        self._visible  = False
        self._tk_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._after_id = None  # scheduled refresh

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def _ensure_tk_thread(self) -> None:
        """Start the Tk main loop in a dedicated thread if not already running."""
        if self._tk_thread and self._tk_thread.is_alive():
            return

        def _run():
            self._root = tk.Tk()
            self._root.withdraw()
            self._root.mainloop()

        self._tk_thread = threading.Thread(target=_run, daemon=True, name="TkPopup")
        self._tk_thread.start()
        time.sleep(0.15)  # let Tk initialise

    def show(self) -> None:
        """Show or bring-to-front the popup. Safe to call from any thread."""
        with self._lock:
            if self._visible:
                if self._win:
                    self._root.after(0, self._win.lift)
                return
            self._ensure_tk_thread()
            self._root.after(0, self._open_window)

    def hide(self) -> None:
        """Close the popup. Safe to call from any thread."""
        with self._lock:
            if not self._visible:
                return
            if self._root:
                self._root.after(0, self._close_window)

    def toggle(self) -> None:
        if self._visible:
            self.hide()
        else:
            self.show()

    # ── Window construction ────────────────────────────────────────────────────

    def _open_window(self) -> None:
        """Called on the Tk thread."""
        self._win = tk.Toplevel(self._root)
        win = self._win
        win.overrideredirect(True)      # frameless
        win.attributes("-topmost", True)
        win.configure(bg=BG)
        win.resizable(False, False)

        self._build_ui(win)
        self._position_window(win)
        self._visible = True

        # Close on Escape
        win.bind("<Escape>", lambda e: self.hide())

        # Close on click-outside (FocusOut)
        win.bind("<FocusOut>", self._on_focus_out)
        win.bind("<Button-1>", self._on_click_inside)
        win.focus_force()

        # Start auto-refresh
        self._schedule_refresh()

    def _close_window(self) -> None:
        """Called on the Tk thread."""
        if self._after_id:
            try:
                self._root.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        if hasattr(self, "_win") and self._win:
            self._win.destroy()
            self._win = None
        self._visible = False

    def _on_focus_out(self, event) -> None:
        # Delay to avoid false positives when clicking child widgets
        self._root.after(100, self._check_focus)

    def _check_focus(self) -> None:
        if not self._visible:
            return
        try:
            fw = self._root.focus_get()
            if fw is None or (hasattr(self, "_win") and not str(fw).startswith(str(self._win))):
                self.hide()
        except Exception:
            pass

    def _on_click_inside(self, event) -> None:
        # Keep focus when clicking inside
        try:
            if hasattr(self, "_win") and self._win:
                self._win.focus_force()
        except Exception:
            pass

    # ── Positioning ────────────────────────────────────────────────────────────

    def _position_window(self, win: tk.Toplevel) -> None:
        win.update_idletasks()
        w  = win.winfo_reqwidth()
        h  = win.winfo_reqheight()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        # Bottom-right, above taskbar
        x  = sw - w - 12
        y  = sh - h - 52   # 52 px above the bottom for taskbar
        # Stay on screen
        x  = max(0, min(x, sw - w))
        y  = max(0, min(y, sh - h))
        win.geometry(f"{w}x{h}+{x}+{y}")

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self, win: tk.Toplevel) -> None:
        from state import app_state

        # ── Header ────────────────────────────────────────────────────────────
        header = tk.Frame(win, bg=BG_HEADER, pady=10)
        header.pack(fill="x", padx=0, pady=0)

        tk.Label(
            header,
            text="ANTIGRAVITY",
            font=("Segoe UI Semibold", 11),
            fg=TEXT_PRI, bg=BG_HEADER,
        ).pack(side="left", padx=(14, 0))

        tk.Label(
            header,
            text="·  quota tracker",
            font=FONT_TITLE,
            fg=TEXT_SEC, bg=BG_HEADER,
        ).pack(side="left", padx=(4, 0))

        # Close button
        close_btn = tk.Label(
            header,
            text="×",
            font=("Segoe UI", 14),
            fg=TEXT_DIM, bg=BG_HEADER,
            cursor="hand2",
        )
        close_btn.pack(side="right", padx=(0, 12))
        close_btn.bind("<Button-1>", lambda e: self.hide())

        # ── Separator ─────────────────────────────────────────────────────────
        tk.Frame(win, bg=BORDER, height=1).pack(fill="x")

        # ── Scrollable accounts area ───────────────────────────────────────────
        accounts_frame = tk.Frame(win, bg=BG)
        accounts_frame.pack(fill="x")
        self._accounts_frame = accounts_frame

        # ── Separator ─────────────────────────────────────────────────────────
        tk.Frame(win, bg=BORDER, height=1).pack(fill="x")

        # ── Activity log ──────────────────────────────────────────────────────
        log_header = tk.Frame(win, bg=BG, pady=6)
        log_header.pack(fill="x", padx=14)
        tk.Label(
            log_header,
            text="ACTIVITY",
            font=("Segoe UI Semibold", 7),
            fg=TEXT_DIM, bg=BG,
            letterSpacing=2,
        ).pack(side="left")

        self._log_frame = tk.Frame(win, bg=BG)
        self._log_frame.pack(fill="x", padx=14, pady=(0, 6))

        # ── Separator ─────────────────────────────────────────────────────────
        tk.Frame(win, bg=BORDER, height=1).pack(fill="x")

        # ── Action buttons ─────────────────────────────────────────────────────
        btn_row = tk.Frame(win, bg=BG, pady=10)
        btn_row.pack(fill="x", padx=14)

        capture_btn = self._make_button(
            btn_row, "⊕  Capture Now",
            command=self._on_capture_now,
            primary=True,
        )
        capture_btn.pack(side="left")
        self._capture_btn = capture_btn

        dash_btn = self._make_button(
            btn_row, "↗  Dashboard",
            command=lambda: webbrowser.open("http://localhost:4300"),
            primary=False,
        )
        dash_btn.pack(side="left", padx=(8, 0))

        # ── Footer ────────────────────────────────────────────────────────────
        tk.Frame(win, bg=BORDER, height=1).pack(fill="x")
        footer = tk.Frame(win, bg=BG_HEADER, pady=7)
        footer.pack(fill="x")
        self._last_cap_label = tk.Label(
            footer,
            text="Last captured: —",
            font=FONT_LABEL,
            fg=TEXT_DIM, bg=BG_HEADER,
        )
        self._last_cap_label.pack(side="left", padx=14)

        # ── Initial render ─────────────────────────────────────────────────────
        self._refresh_content()

    def _make_button(
        self, parent, text: str, command, primary: bool = False
    ) -> tk.Label:
        """Flat label-button (tkinter has no native flat button style)."""
        btn = tk.Label(
            parent,
            text=text,
            font=FONT_SMALL,
            fg=BADGE_FG if primary else TEXT_SEC,
            bg=ACCENT    if primary else BG_CARD,
            padx=10, pady=5,
            cursor="hand2",
        )
        btn.bind("<Button-1>", lambda e: command())
        btn.bind("<Enter>", lambda e: btn.configure(
            bg="#e0e0e0" if primary else "#1e1e1e"
        ))
        btn.bind("<Leave>", lambda e: btn.configure(
            bg=ACCENT if primary else BG_CARD
        ))
        return btn

    # ── Content rendering ──────────────────────────────────────────────────────

    def _refresh_content(self) -> None:
        """Re-render accounts + activity log. Called on Tk thread."""
        from state import app_state

        # ── Accounts ──────────────────────────────────────────────────────────
        for w in self._accounts_frame.winfo_children():
            w.destroy()

        accounts = app_state.get_accounts()

        if not accounts:
            tk.Label(
                self._accounts_frame,
                text="No accounts captured yet.",
                font=FONT_SMALL,
                fg=TEXT_DIM, bg=BG,
            ).pack(padx=14, pady=10)
        else:
            for idx, acct in enumerate(accounts[:5]):   # max 5 cards
                self._build_account_card(
                    self._accounts_frame,
                    acct,
                    is_top=(idx == 0),
                )
                if idx < min(len(accounts), 5) - 1:
                    tk.Frame(
                        self._accounts_frame, bg=BORDER, height=1
                    ).pack(fill="x", padx=14)

        # ── Activity log ──────────────────────────────────────────────────────
        for w in self._log_frame.winfo_children():
            w.destroy()

        events = app_state.get_events()[:MAX_EVENTS]
        if not events:
            tk.Label(
                self._log_frame,
                text="Waiting for first capture…",
                font=FONT_MONO,
                fg=TEXT_DIM, bg=BG,
            ).pack(anchor="w")
        else:
            for ts, level, msg in events:
                self._build_event_row(self._log_frame, ts, level, msg)

        # ── Last captured ─────────────────────────────────────────────────────
        lca = app_state.last_capture_at
        self._last_cap_label.configure(
            text=f"Last captured: {_time_ago(lca)}"
        )

        # ── Capture button state ───────────────────────────────────────────────
        if app_state.is_capturing:
            self._capture_btn.configure(
                text="⧗  Capturing…",
                bg="#1a1a1a",
                fg=TEXT_SEC,
            )
        else:
            self._capture_btn.configure(
                text="⊕  Capture Now",
                bg=ACCENT,
                fg=BADGE_FG,
            )

    def _build_account_card(
        self, parent, acct: dict, is_top: bool
    ) -> None:
        card = tk.Frame(parent, bg=BG_CARD if is_top else BG, pady=10, padx=14)
        card.pack(fill="x")

        # ── Name row ──────────────────────────────────────────────────────────
        name_row = tk.Frame(card, bg=card["bg"])
        name_row.pack(fill="x", pady=(0, 2))

        if is_top:
            badge = tk.Label(
                name_row,
                text=" ★ USE NEXT ",
                font=FONT_BADGE,
                fg=BADGE_FG, bg=BADGE_BG,
                padx=2,
            )
            badge.pack(side="left", padx=(0, 6))

        tk.Label(
            name_row,
            text=acct.get("displayName", acct.get("id", "—")),
            font=FONT_SEMIB if is_top else FONT_MED,
            fg=TEXT_PRI, bg=card["bg"],
            anchor="w",
        ).pack(side="left")

        l = acct.get("latest") or {}

        # ── Claude / GPT bars ──────────────────────────────────────────────────
        self._bar_row(card, "Weekly",  l.get("claude_weekly_pct"),   card["bg"])
        self._bar_row(card, "5-hour",  l.get("claude_fivehour_pct"), card["bg"])

        # ── Reset countdown ───────────────────────────────────────────────────
        reset_in = _time_until(l.get("claude_weekly_reset_at"))
        if reset_in and reset_in != "—":
            tk.Label(
                card,
                text=f"resets in  {reset_in}",
                font=FONT_LABEL,
                fg=TEXT_DIM, bg=card["bg"],
                anchor="w",
            ).pack(fill="x", pady=(4, 0))

    def _bar_row(
        self, parent, label: str, pct: Optional[float], bg: str
    ) -> None:
        row = tk.Frame(parent, bg=bg, pady=2)
        row.pack(fill="x")

        tk.Label(
            row,
            text=label,
            font=FONT_LABEL,
            fg=TEXT_SEC, bg=bg,
            width=7, anchor="w",
        ).pack(side="left")

        bar = ProgressBar(row, width=160)
        bar.pack(side="left", padx=(4, 8))
        bar.set(pct)

        pct_str = f"{round(pct)}%" if pct is not None else "—"
        tk.Label(
            row,
            text=pct_str,
            font=FONT_MONO,
            fg=TEXT_PRI if pct and pct > 30 else (STATUS_WARN if pct and pct > 10 else STATUS_ERR),
            bg=bg,
            width=5, anchor="e",
        ).pack(side="left")

    def _build_event_row(
        self, parent, ts: float, level: str, msg: str
    ) -> None:
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=1)

        dot_color = _event_color(level)
        dot_char  = _event_dot(level)

        # Timestamp
        ts_str = time.strftime("%H:%M", time.localtime(ts))
        tk.Label(
            row,
            text=ts_str,
            font=FONT_MONO,
            fg=TEXT_DIM, bg=BG,
            width=5, anchor="w",
        ).pack(side="left")

        # Dot
        tk.Label(
            row,
            text=dot_char,
            font=FONT_MONO,
            fg=dot_color, bg=BG,
            width=2,
        ).pack(side="left")

        # Message (truncated)
        display_msg = msg[:42] + "…" if len(msg) > 42 else msg
        tk.Label(
            row,
            text=display_msg,
            font=FONT_LABEL,
            fg=TEXT_SEC if level in ("info", "progress") else TEXT_PRI,
            bg=BG,
            anchor="w",
        ).pack(side="left", fill="x")

    # ── Auto-refresh ───────────────────────────────────────────────────────────

    def _schedule_refresh(self) -> None:
        """Schedule next UI refresh. Called on Tk thread."""
        if self._visible and hasattr(self, "_win") and self._win:
            try:
                self._refresh_content()
                self._after_id = self._root.after(2000, self._schedule_refresh)
            except Exception:
                pass

    # ── Capture trigger ────────────────────────────────────────────────────────

    def _on_capture_now(self) -> None:
        if self._fire_capture:
            from state import app_state
            if app_state.is_capturing:
                return  # already in progress
            threading.Thread(
                target=self._fire_capture,
                daemon=True,
                name="ManualTrayCapture",
            ).start()
