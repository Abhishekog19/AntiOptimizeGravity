"""
notifier.py — Antigravity Quota Tracker v3

Polls the Antigravity IDE's Chrome DevTools Protocol (CDP) endpoint every
2 seconds. When the user clicks Sign Out from the profile dropdown the
confirmation dialog ("Sign out of '<name>'?") appears in the Settings DOM.
At that moment this script silently:

  1. Reads the account email from DOM leaf nodes
  2. Clicks the 'Models' nav button and waits for the panel to render
  3. Clicks the last 'Refresh' button (= Models section, not MCP)
  4. Waits 3 s for fresh data to load
  5. Reads innerText, parses quota percentages + reset countdowns
  6. POSTs the reading to the local dashboard at http://localhost:4300
  7. Fires a Windows toast notification with the summary

The capture runs entirely in the background while the confirmation dialog
is still open, so no extra clicks or waiting is needed.

Prerequisites:
  * Antigravity IDE launched with --remote-debugging-port=9222
  * Dashboard running at http://localhost:4300  (node server.js)
  * pip install websocket-client requests  (optional but recommended)
  * pip install win10toast                 (optional, for toast notifications)

Usage:
  python notifier.py              # normal mode
  python notifier.py --dry-run    # log only, skip POST and toasts
"""

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CDP_PORT        = 9222
DASHBOARD_URL   = "http://localhost:4300"
POLL_INTERVAL   = 2          # seconds between DOM checks
DEBOUNCE        = 30         # minimum seconds between captures
SIGN_OUT_MARKER = "Sign out of"   # unique string in confirmation dialog

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

import sys
import time
import json
import re
import datetime
import threading
import urllib.request
import urllib.error
import urllib.parse
import struct
import socket
import base64
import websocket 
try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

DRY_RUN = "--dry-run" in sys.argv

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# ---------------------------------------------------------------------------
# Toast notifications
# ---------------------------------------------------------------------------

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


def toast(message: str, title: str = "Antigravity Quota Tracker") -> None:
    """Fire a Windows toast notification (non-blocking)."""
    log(f"[TOAST] {title} | {message}")
    if DRY_RUN:
        return

    notifier = _get_notifier()
    if notifier is None:
        log("  (no toast library; pip install win10toast)")
        return

    def _fire():
        try:
            if hasattr(notifier, "show_toast"):
                notifier.show_toast(title, message, duration=8, threaded=True)
            elif hasattr(notifier, "notify"):
                notifier.notify(title=title, message=message, timeout=8)
        except Exception as exc:
            log(f"Toast error: {exc}")

    threading.Thread(target=_fire, daemon=True).start()

# ---------------------------------------------------------------------------
# CDP helpers
# ---------------------------------------------------------------------------

def _http_get_json(url: str, timeout: float = 5.0):
    if _HAS_REQUESTS:
        r = _requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def find_settings_target(port: int = CDP_PORT):
    """Return the best CDP target dict, or None if unreachable."""
    try:
        targets = _http_get_json(f"http://localhost:{port}/json")
    except Exception:
        return None
    # Prefer a target with 'setting' in the URL; fall back to any page target
    for t in targets:
        if t.get("type") == "page" and "setting" in t.get("url", "").lower():
            return t
    for t in targets:
        if t.get("type") == "page":
            return t
    return None


# --- Minimal WebSocket client (stdlib only, no dependencies) ----------------

def _ws_evaluate(ws_url: str, expression: str, timeout: float = 8.0):
    """
    Execute a JS expression via Runtime.evaluate over a raw WebSocket.
    Uses websocket-client library if available, otherwise falls back to
    a stdlib-only implementation.
    Returns the result value or None.
    """
    try:
        import websocket
        return _ws_eval_wsclient(websocket, ws_url, expression, timeout)
    except ImportError:
        return _ws_eval_stdlib(ws_url, expression, timeout)


def _ws_eval_wsclient(websocket, ws_url: str, expression: str, timeout: float):
    ws = websocket.create_connection(ws_url, timeout=timeout)
    try:
        msg_id = 1
        ws.send(json.dumps({
            "id": msg_id,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True, "awaitPromise": False},
        }))
        deadline = time.time() + timeout
        while time.time() < deadline:
            ws.settimeout(max(0.1, deadline - time.time()))
            raw = ws.recv()
            resp = json.loads(raw)
            if resp.get("id") == msg_id:
                result = resp.get("result", {}).get("result", {})
                return result.get("value")
    finally:
        ws.close()
    return None


def _ws_eval_stdlib(ws_url: str, expression: str, timeout: float):
    """Pure-stdlib WebSocket client for ws:// (not wss://)."""
    m = re.match(r"ws://([^/:]+):?(\d+)?(/.*)?", ws_url)
    if not m:
        raise ValueError(f"Cannot parse ws_url: {ws_url}")
    host = m.group(1)
    port = int(m.group(2) or 80)
    path = m.group(3) or "/"

    key = base64.b64encode(b"AntigravityV3Key").decode()
    handshake = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )

    sock = socket.create_connection((host, port), timeout=timeout)
    sock.sendall(handshake.encode())

    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            sock.close()
            raise ConnectionError("WebSocket handshake failed")
        buf += chunk

    payload = json.dumps({
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {"expression": expression, "returnByValue": True, "awaitPromise": False},
    }).encode("utf-8")

    mask = b"\x01\x02\x03\x04"
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    plen = len(payload)
    if plen <= 125:
        header = struct.pack("!BB", 0x81, 0x80 | plen) + mask
    elif plen <= 65535:
        header = struct.pack("!BBH", 0x81, 0xFE, plen) + mask
    else:
        header = struct.pack("!BBQ", 0x81, 0xFF, plen) + mask
    sock.sendall(header + masked)

    sock.settimeout(timeout)
    raw = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            break
        if not chunk:
            break
        raw += chunk
        if len(raw) < 2:
            continue
        b1 = raw[1]
        is_masked = (b1 & 0x80) != 0
        flen = b1 & 0x7F
        offset = 2
        if flen == 126:
            if len(raw) < 4:
                continue
            flen = struct.unpack("!H", raw[2:4])[0]
            offset = 4
        elif flen == 127:
            if len(raw) < 10:
                continue
            flen = struct.unpack("!Q", raw[2:10])[0]
            offset = 10
        if is_masked:
            offset += 4
        if len(raw) >= offset + flen:
            frame = raw[offset:offset + flen]
            resp = json.loads(frame.decode("utf-8"))
            result = resp.get("result", {}).get("result", {})
            sock.close()
            return result.get("value")

    sock.close()
    return None


def cdp_evaluate(target: dict, expression: str):
    """Run a JS expression in the target page via CDP. Returns value or None."""
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        return None
    try:
        return _ws_evaluate(ws_url, expression)
    except Exception as exc:
        log(f"CDP evaluate error: {exc}")
        return None


def cdp_get_innertext(target: dict) -> str:
    val = cdp_evaluate(target, "document.documentElement.innerText")
    return val if isinstance(val, str) else ""

# ---------------------------------------------------------------------------
# Parser — bounded section logic
# ---------------------------------------------------------------------------

def _find_line(lines: list, keyword: str):
    kw = keyword.lower()
    for i, line in enumerate(lines):
        if kw in line.lower():
            return i
    return None


def _find_pct(lines: list, from_idx: int, to_idx: int):
    for line in lines[from_idx:to_idx]:
        m = re.search(r"\b(\d{1,3})\s*%", line)
        if m:
            val = int(m.group(1))
            if 0 <= val <= 100:
                return val
    return None


def _find_reset(lines: list, from_idx: int, to_idx: int):
    pattern = re.compile(
        r"(\d+\s+days?(?:[^.\n]*)?|\d+\s+hours?(?:[^.\n]*)?|\d+\s+minutes?(?:[^.\n]*)?)",
        re.IGNORECASE,
    )
    for line in lines[from_idx:to_idx]:
        m = pattern.search(line)
        if m:
            return m.group(0).strip()
    return None


def _extract_number(text: str, unit: str) -> int:
    m = re.search(rf"(\d+)\s+{unit}s?", text, re.IGNORECASE)
    return int(m.group(1)) if m else 0


def parse_reset_to_timestamp(raw_text, captured_at: datetime.datetime):
    if not raw_text:
        return None
    days    = _extract_number(raw_text, "day")
    hours   = _extract_number(raw_text, "hour")
    minutes = _extract_number(raw_text, "minute")
    delta   = datetime.timedelta(days=days, hours=hours, minutes=minutes)
    return (captured_at + delta).isoformat()


def _parse_section(lines: list, start: int, end: int):
    section = lines[start:end]
    wi = _find_line(section, "Weekly Limit")
    fi = _find_line(section, "Five Hour Limit")
    if wi is None or fi is None:
        return None

    weekly_pct     = _find_pct(section,   wi,          fi)
    weekly_reset   = _find_reset(section, wi,          fi)
    fivehour_pct   = _find_pct(section,   fi,          len(section))
    fivehour_reset = _find_reset(section, fi,          len(section))

    if weekly_pct is None or fivehour_pct is None:
        return None
    if weekly_reset and _extract_number(weekly_reset, "day") > 7:
        return None
    if fivehour_reset and _extract_number(fivehour_reset, "hour") > 5:
        return None

    return {
        "weeklyPct":     weekly_pct,
        "weeklyReset":   weekly_reset,
        "fiveHourPct":   fivehour_pct,
        "fiveHourReset": fivehour_reset,
    }


def parse_quota(text: str):
    """Parse innerText from the Models panel. Returns structured dict or None."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    gemini_idx = _find_line(lines, "Gemini Models")
    claude_idx  = _find_line(lines, "Claude and GPT models")
    if claude_idx is None:
        return None
    return {
        "gemini":    _parse_section(lines, gemini_idx, claude_idx) if gemini_idx is not None else None,
        "claudeGpt": _parse_section(lines, claude_idx, len(lines)),
    }

# ---------------------------------------------------------------------------
# Dashboard POST
# ---------------------------------------------------------------------------

def post_reading(email: str, quota: dict, captured_at: datetime.datetime) -> bool:
    if DRY_RUN:
        log(f"[DRY-RUN] Would POST reading for {email}")
        return True

    # Attach resolved reset timestamps
    for section_key in ("gemini", "claudeGpt"):
        section = (quota or {}).get(section_key)
        if section:
            section["weeklyResetAt"]   = parse_reset_to_timestamp(
                section.get("weeklyReset"), captured_at)
            section["fiveHourResetAt"] = parse_reset_to_timestamp(
                section.get("fiveHourReset"), captured_at)

    payload = {
        "accountId":  email,
        "capturedAt": captured_at.isoformat(),
        "quota":      quota,
    }
    url = f"{DASHBOARD_URL}/api/readings"

    try:
        if _HAS_REQUESTS:
            r = _requests.post(url, json=payload, timeout=8)
            r.raise_for_status()
            log(f"  -> POST OK (HTTP {r.status_code})")
            return True
        body = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            log(f"  -> POST OK (HTTP {resp.status})")
            return True
    except Exception as exc:
        log(f"  -> POST failed: {exc}  (dashboard up?)")
        return False

# ---------------------------------------------------------------------------
# Capture sequence
# ---------------------------------------------------------------------------

_state = {"capturing": False, "last_capture_time": 0.0}


def run_capture_sequence(target: dict) -> None:
    """Full capture — runs in a daemon thread."""
    _state["capturing"] = True
    captured_at = datetime.datetime.now()
    log("== Capture sequence started ==================================")
    try:
        # 1. Read email
        email = cdp_evaluate(target, r"""
            Array.from(document.querySelectorAll('*'))
                .filter(el => el.children.length === 0
                           && el.innerText?.includes('@'))
                .map(el => el.innerText.trim())
                .find(t => t.includes('.') && t.length < 100) || null
        """)
        if not email:
            log("  Email: NOT FOUND")
            toast("Capture failed: could not read email")
            return
        log(f"  Email: {email}")

        # 2-3. Navigate to Models tab
        cdp_evaluate(target, r"""
            const btn = [...document.querySelectorAll('button')]
                .find(b => b.innerText.trim() === 'Models');
            if (btn) btn.click();
        """)
        log("  Clicked 'Models' button")
        time.sleep(0.8)

        # 4. Click last Refresh button (= Models section)
        cdp_evaluate(target, r"""
            const btns = [...document.querySelectorAll('button')]
                .filter(b => b.innerText.trim() === 'Refresh');
            if (btns.length) btns[btns.length - 1].click();
        """)
        log("  Clicked 'Refresh' (last)")
        time.sleep(3.0)

        # 5. Read + parse
        text  = cdp_get_innertext(target)
        quota = parse_quota(text)

        if not quota:
            log("  Parse failed — Models panel not rendered?")
            toast(f"Capture failed: parse error for {email}")
            return

        cg  = quota.get("claudeGpt") or {}
        gem = quota.get("gemini")    or {}
        log(f"  Claude/GPT  weekly={cg.get('weeklyPct')}%  5hr={cg.get('fiveHourPct')}%")
        log(f"  Gemini      weekly={gem.get('weeklyPct')}%  5hr={gem.get('fiveHourPct')}%")

        # 6. POST
        ok = post_reading(email, quota, captured_at)

        # 7. Toast
        lines = []
        if cg:
            lines.append(f"Claude/GPT: {cg.get('weeklyPct')}% weekly / {cg.get('fiveHourPct')}% 5hr")
        if gem:
            lines.append(f"Gemini:     {gem.get('weeklyPct')}% weekly / {gem.get('fiveHourPct')}% 5hr")
        if not ok:
            lines.append("(!) Dashboard POST failed")

        toast(
            "\n".join(lines) if lines else "Quota captured",
            title=f"Quota saved — {email}",
        )
        _state["last_capture_time"] = time.time()

    except Exception as exc:
        log(f"  Capture error: {exc}")
        toast(f"Capture error: {exc}")
    finally:
        _state["capturing"] = False
        log("== Capture sequence finished =================================")

# ---------------------------------------------------------------------------
# Main polling loop
# ---------------------------------------------------------------------------

def main() -> None:
    mode = "DRY-RUN" if DRY_RUN else "LIVE"
    log(f"Antigravity Quota Tracker v3  [{mode}]")
    log(f"CDP port={CDP_PORT}  poll={POLL_INTERVAL}s  debounce={DEBOUNCE}s  dashboard={DASHBOARD_URL}")
    log("Waiting for Sign-Out confirmation dialog …\n")

    no_target_warned_at: float = 0.0

    while True:
        try:
            if _state["capturing"]:
                time.sleep(POLL_INTERVAL)
                continue

            target = find_settings_target(port=CDP_PORT)

            if not target:
                if time.time() - no_target_warned_at > 30:
                    log(f"No CDP target — is Antigravity running with "
                        f"--remote-debugging-port={CDP_PORT}?")
                    no_target_warned_at = time.time()
                time.sleep(POLL_INTERVAL)
                continue

            # Reset the "no target" warning once we have a target
            no_target_warned_at = 0.0

            text = cdp_get_innertext(target)

            if SIGN_OUT_MARKER in text:
                elapsed = time.time() - _state["last_capture_time"]
                if elapsed > DEBOUNCE:
                    log(f"Sign-out dialog detected — starting capture (last={elapsed:.0f}s ago)")
                    threading.Thread(
                        target=run_capture_sequence,
                        args=(target,),
                        daemon=True,
                    ).start()
                else:
                    log(f"Sign-out dialog detected — debounced ({elapsed:.0f}s < {DEBOUNCE}s)")

        except Exception as exc:
            log(f"Polling error: {exc}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[notifier] Stopped.")
