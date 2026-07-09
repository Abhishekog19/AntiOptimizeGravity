"""
notifier.py — Antigravity workspace-switch reminder

Polls every 5 seconds for a window with "Antigravity IDE" in its title.
When the first segment of the title (workspace/profile name) changes,
fires a Windows toast notification reminding you to record quota before
losing the context.

IMPORTANT — VALIDATION STEP (per spec):
  Run this script and switch a few Antigravity workspaces while watching
  the console. Confirm that:
    1. The logged title changes match a workspace switch (first segment changes).
    2. File opens/saves do NOT incorrectly trigger the heuristic.
  Only after a day of real use with console logging should you trust the
  toast notifications. The --dry-run flag skips toasts entirely for this
  validation phase.

Usage:
  python notifier.py              # normal mode — logs + fires toasts
  python notifier.py --dry-run    # console only, no toasts (validation mode)

Dependencies (pip install):
  pywin32       (win32gui)
  win10toast    (preferred) OR plyer (fallback)
"""

import sys
import time
import webbrowser
import datetime
import win32gui

POLL_INTERVAL_SECONDS = 5
DEBOUNCE_SECONDS = 60
DASHBOARD_URL = "http://localhost:4300"
TITLE_KEYWORD = "Antigravity IDE"

DRY_RUN = "--dry-run" in sys.argv

# ── Toast helper (tries win10toast, falls back to plyer) ─────────────────────

_notifier = None

def _get_notifier():
    global _notifier
    if _notifier is not None:
        return _notifier
    try:
        from win10toast import ToastNotifier
        _notifier = ToastNotifier()
        return _notifier
    except ImportError:
        pass
    try:
        from plyer import notification as plyer_notif
        _notifier = plyer_notif
        return _notifier
    except ImportError:
        pass
    return None


def fire_toast(title: str, message: str) -> None:
    if DRY_RUN:
        print(f"[DRY-RUN] Would fire toast: {title!r} — {message!r}")
        return

    notifier = _get_notifier()
    if notifier is None:
        print(
            "[notifier] ⚠  No toast library found. "
            "Install win10toast or plyer:  pip install win10toast"
        )
        return

    # win10toast
    if hasattr(notifier, "show_toast"):
        notifier.show_toast(
            title,
            message,
            duration=10,
            threaded=True,
        )
        return

    # plyer
    if hasattr(notifier, "notify"):
        notifier.notify(
            title=title,
            message=message,
            timeout=10,
        )

# ── Window enumeration ────────────────────────────────────────────────────────

def get_antigravity_titles() -> list[str]:
    """Return all visible window titles containing TITLE_KEYWORD."""
    titles = []
    def _callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            text = win32gui.GetWindowText(hwnd)
            if TITLE_KEYWORD in text:
                titles.append(text)
    win32gui.EnumWindows(_callback, None)
    return titles


def workspace_segment(title: str) -> str:
    """
    Extract the workspace/profile segment from an Antigravity title.

    Expected format: "<workspace> - Antigravity IDE - <file>"
    We watch the FIRST segment (before " - "), which changes on account/
    workspace switch but should NOT change on file open.

    Returns the raw title as fallback if no " - " separator is found.
    """
    parts = title.split(" - ")
    return parts[0].strip() if len(parts) >= 2 else title.strip()


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    mode = "DRY-RUN (console only)" if DRY_RUN else "LIVE (toasts enabled)"
    print(f"[notifier] Starting in {mode} mode.")
    print(f"[notifier] Watching for '{TITLE_KEYWORD}' window title every {POLL_INTERVAL_SECONDS}s.")
    print(f"[notifier] Debounce: {DEBOUNCE_SECONDS}s between notifications.")
    if DRY_RUN:
        print("[notifier] Run without --dry-run once you've validated the title-change heuristic.")
    print()

    last_workspace: str | None = None
    last_notification_time: float = 0.0

    while True:
        try:
            titles = get_antigravity_titles()

            if not titles:
                # Window not open — reset tracking so we don't fire on re-open
                if last_workspace is not None:
                    ts = datetime.datetime.now().strftime("%H:%M:%S")
                    print(f"[{ts}] Antigravity window closed or not found. Resetting tracker.")
                    last_workspace = None

            else:
                # Use the first found window (most users have one Antigravity process)
                title = titles[0]
                workspace = workspace_segment(title)
                ts = datetime.datetime.now().strftime("%H:%M:%S")

                if last_workspace is None:
                    # First detection — just record, don't fire
                    print(f"[{ts}] Detected workspace: {workspace!r}  (title: {title!r})")
                    last_workspace = workspace

                elif workspace != last_workspace:
                    # Workspace changed
                    now = time.time()
                    debounced = (now - last_notification_time) < DEBOUNCE_SECONDS
                    print(
                        f"[{ts}] Workspace changed: {last_workspace!r} → {workspace!r}"
                        + ("  [debounced]" if debounced else "")
                    )

                    if not debounced:
                        fire_toast(
                            "Antigravity workspace switched",
                            f"Did you record quota for '{last_workspace}'? "
                            f"Click to open the dashboard.",
                        )
                        # Try to open dashboard in browser (works from toast on some versions)
                        try:
                            webbrowser.open(DASHBOARD_URL)
                        except Exception:
                            pass
                        last_notification_time = now

                    last_workspace = workspace

                else:
                    # Title changed (e.g. file opened) but workspace segment is the same — ignore
                    if title != titles[0]:  # would only differ if we tracked title separately
                        pass  # No-op: file-level changes are filtered by workspace_segment()

        except Exception as exc:
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] Error: {exc}")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[notifier] Stopped.")
