"""
notifier.py — Antigravity Quota Tracker v3

Polls every 2 seconds for the profile dropdown Sign Out button in Antigravity IDE.
When the user clicks their profile icon, the dropdown renders a 'Sign Out'
button element in the editor DOM. This notifier detects it via a targeted
element query (NOT innerText scan) which is immune to false positives from
chat/editor content.

At that moment this script silently:
  1. Reads the account email from the dropdown DOM (near the Sign Out button)
  2. Uses the Settings > Models page (settingsScreen CDP target) for quota
  3. Clicks the last 'Refresh' button on the Models panel
  4. Waits 3 s, reads innerText, parses quota percentages + reset countdowns
  5. POSTs the reading to the local dashboard at http://localhost:4300
  6. Fires a Windows toast notification with the summary

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

CDP_PORT       = 9222
DASHBOARD_URL  = "http://localhost:4300"
POLL_INTERVAL  = 2          # seconds between DOM checks
DEBOUNCE       = 30         # minimum seconds between captures

# JS expression evaluated in each editor page target every POLL_INTERVAL.
# Returns the user email string if the Sign Out button is visible in the DOM
# (i.e. the profile dropdown is open), otherwise returns null.
# Uses element queries — NOT innerText — so chat/editor text never triggers it.
DROPDOWN_DETECT_JS = r"""
(function() {
    // Find a Sign Out button/menuitem currently visible in the DOM
    const candidates = [
        ...document.querySelectorAll(
            'button, li, a, [role="menuitem"], [role="option"], [role="listitem"]'
        )
    ];
    const signOutEl = candidates.find(
        el => el.innerText && el.innerText.trim() === 'Sign Out'
    );
    if (!signOutEl) return null;

    // Sign Out button is visible: now find the email in the surrounding dropdown.
    // Walk up to the dropdown root, then find the email-like text node.
    let root = signOutEl.parentElement;
    for (let i = 0; i < 8 && root; i++) {
        const text = root.innerText || '';
        const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
        const email = lines.find(
            l => l.length < 100 && !l.includes(' ') &&
                 l.indexOf('@') > 0 &&
                 l.indexOf('@') === l.lastIndexOf('@') &&
                 l.lastIndexOf('.') > l.indexOf('@')
        );
        if (email) return email;
        root = root.parentElement;
    }

    // Email not found in ancestors: return sentinel so we know dropdown IS open
    return '__dropdown_open__';
})()
"""

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
    line = f"[{ts}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", errors="replace").decode("ascii"), flush=True)

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


# Simple email pattern: no whitespace, exactly one @, at least one dot after @
_EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')


def _get_all_page_targets(port: int = CDP_PORT) -> list:
    """Return all CDP page targets, or [] if the port is unreachable."""
    try:
        return [t for t in _http_get_json(f"http://localhost:{port}/json")
                if t.get("type") == "page"]
    except Exception:
        return []


def find_settings_target(port: int = CDP_PORT):
    """
    Return the CDP target for the Antigravity Settings page, or None.
    Requires 'settingsScreen' in the URL — exactly as test-e.js uses it.
    Does NOT fall back to other page targets to avoid reading the editor window.
    """
    try:
        targets = _http_get_json(f"http://localhost:{port}/json")
    except Exception:
        return None
    for t in targets:
        if t.get("type") == "page" and "settingsScreen" in t.get("url", ""):
            return t
    return None


def scan_for_signout(port: int = CDP_PORT):
    """
    Scan ALL CDP page targets for the Sign-out confirmation dialog text.
    Returns (target, innerText) for the first matching target, or (None, '').

    The Sign-out dialog is a workbench-level modal in Electron — it may appear
    in any page target's DOM, not necessarily in 'settingsScreen'.
    settingsScreen is still preferred when present (for email + quota reads).
    """
    pages = _get_all_page_targets(port)
    if not pages:
        return None, ""

    # Prefer settingsScreen; put it first so it's checked before editor windows
    pages.sort(key=lambda t: (0 if "settingsScreen" in t.get("url", "") else 1))

    for t in pages:
        text = cdp_get_innertext(t)
        if SIGN_OUT_MARKER in text:
            return t, text

    return None, ""


# --- Minimal WebSocket client (stdlib only, no dependencies) ----------------

def _ws_evaluate(ws_url: str, expression: str, timeout: float = 8.0):
    """
    Execute a JS expression via Runtime.evaluate over a raw WebSocket.
    Always tries the stdlib implementation first (no Origin header sent,
    so Electron's CDP server never returns 403 Forbidden).
    Falls back to websocket-client if stdlib fails for any other reason.
    Returns the result value or None.
    """
    # Stdlib path first — sends no Origin header, avoids Electron 403
    try:
        return _ws_eval_stdlib(ws_url, expression, timeout)
    except Exception:
        pass
    # websocket-client fallback (pass origin="" to suppress auto-Origin header)
    try:
        import websocket
        return _ws_eval_wsclient(websocket, ws_url, expression, timeout)
    except ImportError:
        pass
    except Exception:
        pass
    return None


def _ws_eval_wsclient(websocket, ws_url: str, expression: str, timeout: float):
    # Pass origin="" so websocket-client does not auto-set
    # 'Origin: http://localhost:9222' which Electron's CDP rejects with 403.
    ws = websocket.create_connection(ws_url, timeout=timeout, origin="")
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


def run_capture_sequence(dropdown_target: dict) -> None:
    """
    Full capture sequence — runs in a daemon thread.

    Email is read from the settingsScreen target by navigating to Account
    via history.pushState (confirmed working in test-d.js line 18).
    Quota is read from the same target navigated back to Models.
    """
    _state["capturing"] = True
    captured_at = datetime.datetime.now()
    log("== Capture sequence started ==================================")
    try:
        # 1. Find the Settings target (settingsScreen URL).
        #    All email + quota reads happen here on the clean Settings page.
        settings_target = find_settings_target(port=CDP_PORT)
        if not settings_target:
            log("  Settings target not found -- open Settings > Models before signing out")
            toast("Capture failed: open Settings > Models first")
            return
        log(f"  Settings target: {settings_target.get('url','')[:70]}")

        # 2. Navigate to Account page via history.pushState (test-d.js line 18).
        cdp_evaluate(settings_target,
                     "history.pushState({}, '', '/?settingsScreen=Account')")
        log("  Navigated to Account page")
        time.sleep(1.5)

        # 3. Read email using regex scan on full innerText (test-d.js lines 28-33).
        #    This is the confirmed-working approach from the test scripts.
        email_raw = cdp_evaluate(settings_target, r"""
            (function() {
                const text = document.documentElement.innerText;
                const emailRegex = /[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}/g;
                const matches = [...new Set(text.match(emailRegex) || [])];
                return matches.find(m => m.length < 100 && m.indexOf('.') > m.indexOf('@'))
                    || null;
            })()
        """)
        log(f"  Email (regex scan): {email_raw!r}")
        email = email_raw if (email_raw and _EMAIL_RE.match(str(email_raw))) else None

        # Fallback: test-e.js leaf-node scan (lines 26-35)
        if not email:
            email_leaf = cdp_evaluate(settings_target, r"""
                (function() {
                    const allElements = Array.from(document.querySelectorAll('*'));
                    let emailEl = null;
                    for (const el of allElements) {
                        if (el.children.length === 0 && el.innerText &&
                            el.innerText.includes('@') && el.innerText.includes('.')) {
                            emailEl = el;
                        }
                    }
                    return emailEl ? emailEl.innerText.trim() : null;
                })()
            """)
            log(f"  Email (leaf scan): {email_leaf!r}")
            if email_leaf and _EMAIL_RE.match(str(email_leaf)):
                email = email_leaf

        if not email:
            log("  Email: NOT FOUND on Account page")
            toast("Capture failed: could not read email from Settings > Account")
            return
        log(f"  Email: {email}")

        # 4. Navigate back to Models page (history.pushState).
        cdp_evaluate(settings_target,
                     "history.pushState({}, '', '/?settingsScreen=Models')")
        log("  Navigated back to Models")
        time.sleep(0.8)

        # 5. Click the last Refresh button (= Models section, not MCP).
        cdp_evaluate(settings_target, r"""
            const btns = [...document.querySelectorAll('button')]
                .filter(b => b.innerText.trim() === 'Refresh');
            if (btns.length) btns[btns.length - 1].click();
        """)
        log("  Clicked 'Refresh' (last)")
        time.sleep(3.0)

        # 6. Read and parse quota.
        text  = cdp_get_innertext(settings_target)
        quota = parse_quota(text)

        if not quota:
            log("  Parse failed -- Settings > Models not loaded?")
            toast(f"Capture failed: parse error for {email}")
            return

        cg  = quota.get("claudeGpt") or {}
        gem = quota.get("gemini")    or {}
        log(f"  Claude/GPT  weekly={cg.get('weeklyPct')}%  5hr={cg.get('fiveHourPct')}%")
        log(f"  Gemini      weekly={gem.get('weeklyPct')}%  5hr={gem.get('fiveHourPct')}%")

        # 7. POST to dashboard.
        ok = post_reading(email, quota, captured_at)

        # 8. Toast.
        msg_lines = []
        if cg:
            msg_lines.append(f"Claude/GPT: {cg.get('weeklyPct')}% weekly / {cg.get('fiveHourPct')}% 5hr")
        if gem:
            msg_lines.append(f"Gemini:     {gem.get('weeklyPct')}% weekly / {gem.get('fiveHourPct')}% 5hr")
        if not ok:
            msg_lines.append("(!) Dashboard POST failed")

        toast(
            "\n".join(msg_lines) if msg_lines else "Quota captured",
            title=f"Quota saved - {email}",
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
    log("Trigger: profile dropdown Sign Out button detected via element query")
    log("")

    no_pages_warned_at: float = 0.0

    while True:
        try:
            if _state["capturing"]:
                time.sleep(POLL_INTERVAL)
                continue

            pages = _get_all_page_targets(port=CDP_PORT)

            if not pages:
                if time.time() - no_pages_warned_at > 30:
                    log(f"No CDP page targets -- is Antigravity running with "
                        f"--remote-debugging-port={CDP_PORT}?")
                    no_pages_warned_at = time.time()
                time.sleep(POLL_INTERVAL)
                continue

            no_pages_warned_at = 0.0

            # Run DROPDOWN_DETECT_JS on each editor page target.
            # This queries for an actual 'Sign Out' button element —
            # immune to chat/editor text false positives.
            found_target  = None
            found_result  = None
            for t in pages:
                if "settingsScreen" in t.get("url", ""):
                    continue   # skip — dropdown never renders in Settings page
                result = cdp_evaluate(t, DROPDOWN_DETECT_JS)
                if result:   # non-null = Sign Out button visible
                    found_target = t
                    found_result = result
                    break

            if found_target:
                elapsed = time.time() - _state["last_capture_time"]
                url_short = found_target.get("url", "")[:60]
                if elapsed > DEBOUNCE:
                    log(f"Sign Out button detected in: {url_short!r}")
                    log(f"  detect result: {found_result!r}  (debounce: {elapsed:.0f}s)")
                    threading.Thread(
                        target=run_capture_sequence,
                        args=(found_target,),
                        daemon=True,
                    ).start()
                else:
                    log(f"Sign Out button detected -- debounced "
                        f"({elapsed:.0f}s < {DEBOUNCE}s)")

        except Exception as exc:
            log(f"Polling error: {exc}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[notifier] Stopped.")
