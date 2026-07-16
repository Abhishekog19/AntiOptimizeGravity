"""
state.py — Shared application state for Antigravity Quota Tracker

Thread-safe singleton that bridges the three layers:
  • notifier (CDP watcher) → writes capture events + status
  • Flask server           → reads accounts for /api/accounts
  • Tray popup             → reads everything for display
"""

from __future__ import annotations
import threading
import time
from collections import deque
from typing import Optional


class AppState:
    """
    Central hub for live application state.

    All public methods are thread-safe (protected by self._lock).
    """

    # Event levels used by the popup renderer
    LEVEL_INFO     = "info"
    LEVEL_PROGRESS = "progress"
    LEVEL_OK       = "ok"
    LEVEL_WARN     = "warn"
    LEVEL_ERROR    = "error"

    def __init__(self, max_events: int = 30) -> None:
        self._lock = threading.Lock()

        # ── Event log ─────────────────────────────────────────────────────────
        # Each entry: (timestamp_float, level_str, message_str)
        self._events: deque = deque(maxlen=max_events)

        # ── Capture bookkeeping ───────────────────────────────────────────────
        self._capturing: bool       = False
        self._last_capture_at: Optional[float] = None   # time.time()
        self._last_trigger: Optional[str]       = None
        self._trigger_count: int    = 0

        # ── Account snapshot (refreshed after every successful capture) ───────
        # Shape mirrors listAccountsWithLatest() from db.py
        self._accounts: list = []

        # ── Flask server health ───────────────────────────────────────────────
        self._flask_ready: bool = False

    # ── Event log ─────────────────────────────────────────────────────────────

    def log(self, message: str, level: str = LEVEL_INFO) -> None:
        """Append an event to the live log (shown in the popup)."""
        with self._lock:
            self._events.appendleft((time.time(), level, message))

    def get_events(self) -> list:
        """Return a snapshot of events, newest-first."""
        with self._lock:
            return list(self._events)

    # ── Capture state ─────────────────────────────────────────────────────────

    def set_capturing(self, value: bool, trigger: str = "") -> None:
        with self._lock:
            self._capturing = value
            if value:
                msg = f"Capturing ({trigger})…" if trigger else "Capturing…"
                self._events.appendleft((time.time(), self.LEVEL_PROGRESS, msg))

    def set_capture_complete(self, trigger: str, email: str, ok: bool) -> None:
        with self._lock:
            self._capturing = False
            self._last_capture_at = time.time()
            self._last_trigger    = trigger
            self._trigger_count  += 1
            level = self.LEVEL_OK if ok else self.LEVEL_ERROR
            msg   = (
                f"Captured — {email}  [{trigger}]"
                if ok
                else f"Capture failed — {email}  [{trigger}]"
            )
            self._events.appendleft((time.time(), level, msg))

    def set_capture_error(self, trigger: str, detail: str) -> None:
        with self._lock:
            self._capturing = False
            self._events.appendleft(
                (time.time(), self.LEVEL_ERROR, f"Error [{trigger}]: {detail}")
            )

    @property
    def is_capturing(self) -> bool:
        with self._lock:
            return self._capturing

    @property
    def last_capture_at(self) -> Optional[float]:
        with self._lock:
            return self._last_capture_at

    @property
    def last_trigger(self) -> Optional[str]:
        with self._lock:
            return self._last_trigger

    @property
    def trigger_count(self) -> int:
        with self._lock:
            return self._trigger_count

    # ── Accounts cache ────────────────────────────────────────────────────────

    def set_accounts(self, accounts: list) -> None:
        with self._lock:
            self._accounts = list(accounts)

    def get_accounts(self) -> list:
        with self._lock:
            return list(self._accounts)

    # ── Flask health ──────────────────────────────────────────────────────────

    def set_flask_ready(self, value: bool) -> None:
        with self._lock:
            self._flask_ready = value

    @property
    def flask_ready(self) -> bool:
        with self._lock:
            return self._flask_ready


# ── Module-level singleton ─────────────────────────────────────────────────────
app_state = AppState()
