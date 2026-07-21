#!/usr/bin/env python3
"""
notifier/notifier.py  —  Antigravity Quota Tracker v3.0

Five capture triggers
─────────────────────
  1. LAUNCH        Antigravity process appears   → Refresh + read
  2. PROFILE_MENU  Profile dropdown opens        → Refresh + read
  3. SIGN_OUT      Sign-out dialog appears       → Refresh + wait 3s + read
  4. MANUAL        User clicked Refresh in UI    → read immediately (already fresh)
  5. POST_CLOSE    Antigravity process exits     → relaunch silently, Refresh, read, kill

Trigger 4 is the only one that skips the Refresh step because the user just
clicked it — the data is already fresh.  All other triggers click Refresh and
wait 3 s to ensure they read the latest server-side data.

Architecture
────────────
  • CdpSession  — persistent stdlib WebSocket to the settingsScreen target.
                  Reconnects automatically when the connection drops.
                  One connection is maintained and reused for all quota reads.
  • One-shot cdp_evaluate()  — for the cheap 2-second poll on transient
                               editor page targets (profile menu / sign-out).
  • CHEAP_TRIGGER_JS  — minimal querySelectorAll, single CDP round-trip/poll.
  • Structured logging  — levels DEBUG/INFO/WARN/ERROR, ASCII-safe terminal.
  • Heartbeat  — POST /api/heartbeat every 15 s so the dashboard can show a
                 live / stale / offline status dot.

Configuration  (notifier/.env  or  environment variables)
──────────────
  CDP_PORT                  9222
  POLL_INTERVAL_SECONDS     2
  DEBOUNCE_SECONDS          30
  DASHBOARD_URL             http://localhost:4300
  DASHBOARD_API_KEY         (empty = open)
  LOG_LEVEL                 INFO   (DEBUG | INFO | WARN | ERROR)

Usage
─────
  python notifier/notifier.py              # live mode
  python notifier/notifier.py --dry-run    # log only, no POST / toasts
  python notifier/notifier.py --verbose    # DEBUG-level logging

Known limitation — post-close accuracy
───────────────────────────────────────
  When Antigravity exits, this notifier relaunches it in the background,
  navigates to Settings > Models, reads the quota (Refresh + 3 s wait),
  then terminates the relaunched instance.  The Settings panel may briefly
  flash on screen if Electron's Browser.setWindowBounds is unavailable.
  See README.md § "Known Limitations" for details.
"""

# ── stdlib ───────────────────────────────────────────────────────────────────
from __future__ import annotations
import sys, os, time, json, re, datetime, threading, subprocess
import urllib.request, urllib.error
import struct, socket, base64
from pathlib import Path
from typing import Optional

# ── Optional dependencies ─────────────────────────────────────────────────────
try:
    import psutil as _psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

# ── AppState (optional — graceful if run standalone) ──────────────────────────
try:
    import sys as _sys
    _project_root = str(Path(__file__).parent.parent)
    if _project_root not in _sys.path:
        _sys.path.insert(0, _project_root)
    from state import app_state as _app_state
    _HAS_APP_STATE = True
except ImportError:
    _HAS_APP_STATE = False
    _app_state = None  # type: ignore

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

def _load_env(path: Path) -> dict:
    """Parse KEY=VALUE lines from a .env file. Ignores blank lines and comments."""
    env: dict = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env

# Load .env from same directory as this script
_env = _load_env(Path(__file__).parent / ".env")

def _cfg(key: str, default):
    """Read: .env → environment variable → default."""
    val = _env.get(key) or os.environ.get(key)
    if val is None:
        return default
    if isinstance(default, int):
        try:
            return int(val)
        except ValueError:
            return default
    if isinstance(default, float):
        try:
            return float(val)
        except ValueError:
            return default
    return val

CDP_PORT           = _cfg("CDP_PORT",                  9222)
POLL_INTERVAL      = _cfg("POLL_INTERVAL_SECONDS",     2)
DEBOUNCE           = _cfg("DEBOUNCE_SECONDS",          2)   # 2s: re-arm quickly after capture
SAFETY_NET_INTERVAL = _cfg("SAFETY_NET_INTERVAL",      1200) # 20 min
DASHBOARD_URL      = _cfg("DASHBOARD_URL",             "http://localhost:4300")
DASHBOARD_API_KEY  = _cfg("DASHBOARD_API_KEY",         "")
LOG_LEVEL          = _cfg("LOG_LEVEL",                 "INFO").upper()

HEARTBEAT_INTERVAL = 15    # seconds between heartbeat POSTs
RELAUNCH_TIMEOUT   = 30    # kept for CDP wait helper (used by ensure_settings_open)
RELAUNCH_SETTLE    = 3     # seconds to wait for UI to settle after launch

DRY_RUN = "--dry-run" in sys.argv
VERBOSE  = "--verbose" in sys.argv or LOG_LEVEL == "DEBUG"

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

_LEVEL_RANK = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
_EFFECTIVE_LEVEL = 0 if VERBOSE else _LEVEL_RANK.get(LOG_LEVEL, 1)


def log(msg: str, level: str = "INFO") -> None:
    """
    Structured log with level filtering.
    Falls back to ASCII encoding for cp1252-restricted terminals (Windows).
    """
    if _LEVEL_RANK.get(level, 1) < _EFFECTIVE_LEVEL:
        return
    ts   = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{ts} {level:5}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", errors="replace").decode("ascii"), flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Toast notifications
# ─────────────────────────────────────────────────────────────────────────────

_notifier_instance = None


def _get_notifier():
    global _notifier_instance
    if _notifier_instance is not None:
        return _notifier_instance
    # Try win10toast first, then plyer
    for mod_name, attr in [("win10toast", None), ("plyer", "notification")]:
        try:
            mod = __import__(mod_name)
            _notifier_instance = getattr(mod, attr) if attr else mod.ToastNotifier()
            return _notifier_instance
        except (ImportError, AttributeError):
            pass
    return None


def toast(message: str, title: str = "Antigravity Quota Tracker") -> None:
    """Fire a Windows toast notification (non-blocking)."""
    log(f"[TOAST] {title} | {message}")
    if DRY_RUN:
        return
    n = _get_notifier()
    if n is None:
        log("No toast library available (pip install win10toast)", level="DEBUG")
        return

    def _fire():
        try:
            if hasattr(n, "show_toast"):
                n.show_toast(title, message, duration=8, threaded=True)
            elif hasattr(n, "notify"):
                n.notify(title=title, message=message, timeout=8)
        except Exception as exc:
            log(f"Toast error: {exc}", level="WARN")

    threading.Thread(target=_fire, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────────────────────────

def _http_get_json(url: str, timeout: float = 5.0):
    if _HAS_REQUESTS:
        r = _requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _http_post_json(url: str, body: dict, timeout: float = 8.0) -> bool:
    """POST JSON body to url. Returns True on 2xx, False otherwise."""
    try:
        headers = {"Content-Type": "application/json"}
        if DASHBOARD_API_KEY:
            headers["Authorization"] = f"Bearer {DASHBOARD_API_KEY}"
        if _HAS_REQUESTS:
            r = _requests.post(url, json=body, headers=headers, timeout=timeout)
            r.raise_for_status()
            return True
        data = json.dumps(body).encode("utf-8")
        req  = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception as exc:
        log(f"POST {url} failed: {exc}", level="WARN")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CDP helpers — one-shot (for transient editor targets)
# ─────────────────────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')


def _get_all_page_targets(port: int = CDP_PORT) -> list:
    """Return all CDP page targets on the given port, or []."""
    try:
        return [t for t in _http_get_json(f"http://localhost:{port}/json")
                if t.get("type") == "page"]
    except Exception:
        return []


def check_cdp_port(port: int = CDP_PORT) -> str:
    """
    Probe port and return a status string:
      'ok'            — port is open AND returning valid CDP JSON
      'conflict'      — port is open but NOT returning valid CDP JSON
                        (some other HTTP server is occupying the port)
      'not_open'      — connection refused (Antigravity not running with the flag)

    'Conflict' detection logic: a valid CDP /json response is a JSON *list* whose
    items are dicts that each have a 'webSocketDebuggerUrl' key.  If the response
    is not a list, or the list is empty, or items lack that key, we classify it as
    a conflict rather than valid CDP.  This prevents mis-classifying a successful
    response from an unrelated HTTP service as "CDP is working".
    """
    try:
        data = _http_get_json(f"http://localhost:{port}/json", timeout=3.0)
        # Valid CDP: must be a list of target dicts with webSocketDebuggerUrl
        if not isinstance(data, list):
            return "conflict"
        # An empty list is fine — Antigravity may be loading (no pages yet).
        # But if items exist and none have webSocketDebuggerUrl, it's not CDP.
        if data and not any("webSocketDebuggerUrl" in t for t in data if isinstance(t, dict)):
            return "conflict"
        return "ok"
    except (OSError, ConnectionRefusedError):
        return "not_open"
    except Exception:
        # Timeout, non-JSON response, etc. — treat as conflict (something responded
        # but it wasn't CDP-shaped).
        return "conflict"


def _is_settings_panel(t: dict) -> bool:
    """
    Return True if a CDP target IS the floating Settings panel (not the main editor).

    Root-cause discovery: Antigravity IDE sets ALL page target URLs to
    'vscode-file://vscode-app/?settingsScreen=...' (including the main editor
    window when it opens Settings in the same window).  The ONLY reliable
    way to identify the actual floating Settings panel is by its CDP title,
    which is exactly 'Settings'.  The main editor window always has the
    filename + 'Antigravity IDE' in the title.

    We fall back to URL-contains if the title is empty (headless tests etc).
    """
    title = t.get("title", "")
    url   = t.get("url", "")
    if title:
        return title.strip() == "Settings"
    # Fallback: URL-based (original behaviour)
    return "settingsScreen" in url


def find_settings_target(port: int = CDP_PORT) -> Optional[dict]:
    """Return the CDP target for the Antigravity Settings panel, or None."""
    try:
        targets = _http_get_json(f"http://localhost:{port}/json")
    except Exception:
        return None
    for t in targets:
        if t.get("type") == "page" and _is_settings_panel(t):
            return t
    return None


def _ws_eval_stdlib(ws_url: str, expression: str, timeout: float = 8.0):
    """
    Pure-stdlib one-shot CDP Runtime.evaluate over WebSocket.
    Sends no Origin header, so Electron's CDP server never returns 403 Forbidden.
    """
    m = re.match(r"ws://([^/:]+):?(\d+)?(/.*)?", ws_url)
    if not m:
        raise ValueError(f"Bad ws_url: {ws_url}")
    host  = m.group(1)
    port  = int(m.group(2) or 80)
    path  = m.group(3) or "/"

    key       = base64.b64encode(b"AntigravityV3Key").decode()
    handshake = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
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
        "id": 1, "method": "Runtime.evaluate",
        "params": {"expression": expression, "returnByValue": True, "awaitPromise": False},
    }).encode("utf-8")
    mask   = b"\x01\x02\x03\x04"
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    plen   = len(payload)
    if plen <= 125:
        header = struct.pack("!BB", 0x81, 0x80 | plen) + mask
    elif plen <= 65535:
        header = struct.pack("!BBH", 0x81, 0xFE, plen) + mask
    else:
        header = struct.pack("!BBQ", 0x81, 0xFF, plen) + mask
    sock.sendall(header + masked)

    sock.settimeout(timeout)
    raw      = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw += sock.recv(65536)
        except socket.timeout:
            break
        if len(raw) < 2:
            continue
        b1   = raw[1]
        flen = b1 & 0x7F
        off  = 2
        if flen == 126:
            if len(raw) < 4:
                continue
            flen = struct.unpack("!H", raw[2:4])[0]
            off  = 4
        elif flen == 127:
            if len(raw) < 10:
                continue
            flen = struct.unpack("!Q", raw[2:10])[0]
            off  = 10
        if b1 & 0x80:  # masked frame (server → client should NOT be masked, but handle it)
            off += 4
        if len(raw) >= off + flen:
            resp = json.loads(raw[off:off + flen].decode("utf-8"))
            sock.close()
            return resp.get("result", {}).get("result", {}).get("value")

    sock.close()
    return None


def cdp_evaluate(target: dict, expression: str, timeout: float = 5.0):
    """One-shot evaluate on a transient (editor) target. Returns value or None."""
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        return None
    try:
        return _ws_eval_stdlib(ws_url, expression, timeout)
    except Exception as exc:
        log(f"CDP one-shot error: {exc}", level="DEBUG")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# CdpSession — persistent WebSocket to the settingsScreen target
# ─────────────────────────────────────────────────────────────────────────────

class CdpSession:
    """
    Thread-safe, persistent CDP WebSocket connection to one target.

    Design
    ──────
    • A single open socket is maintained and reused across evaluate() calls.
    • A background thread reads incoming frames and dispatches responses
      to the waiting caller via threading.Event.
    • Up to 3 retries with linear backoff on connection failure.
    • close() terminates the reader thread and the socket.

    Usage
    ─────
      sess = CdpSession(ws_url)
      result = sess.evaluate("1 + 1")   # → 2
      sess.close()
    """

    def __init__(self, ws_url: str) -> None:
        self._ws_url  = ws_url
        self._sock: Optional[socket.socket] = None
        self._lock    = threading.Lock()
        self._next_id = 1
        self._pending: dict = {}   # msg_id → {"event": Event, "value": Any}
        self._closed  = False
        self._recv_th: Optional[threading.Thread] = None

    # ── Connection management ─────────────────────────────────────────────────

    def _connect(self) -> None:
        m    = re.match(r"ws://([^/:]+):?(\d+)?(/.*)?", self._ws_url)
        host = m.group(1)
        port = int(m.group(2) or 80)
        path = m.group(3) or "/"
        key  = base64.b64encode(b"AntigravityV3Persist").decode()
        hs   = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        sock = socket.create_connection((host, port), timeout=10)
        sock.sendall(hs.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                sock.close()
                raise ConnectionError("Handshake failed")
            buf += chunk
        self._sock = sock
        self._recv_th = threading.Thread(
            target=self._recv_loop, daemon=True, name="CdpRecv"
        )
        self._recv_th.start()
        log("CdpSession: connected", level="DEBUG")

    def _ensure_connected(self) -> None:
        if self._closed:
            raise RuntimeError("CdpSession is closed")
        if self._sock is None:
            self._connect()

    def _recv_loop(self) -> None:
        """Background reader: parse frames and wake up waiting callers."""
        my_sock = self._sock
        raw     = b""
        while not self._closed and my_sock is self._sock:
            try:
                my_sock.settimeout(1.0)
                chunk = my_sock.recv(65536)
                if not chunk:
                    break
                raw += chunk
                # Parse as many complete frames as possible
                while True:
                    if len(raw) < 2:
                        break
                    b1   = raw[1]
                    flen = b1 & 0x7F
                    off  = 2
                    if flen == 126:
                        if len(raw) < 4:
                            break
                        flen = struct.unpack("!H", raw[2:4])[0]
                        off  = 4
                    elif flen == 127:
                        if len(raw) < 10:
                            break
                        flen = struct.unpack("!Q", raw[2:10])[0]
                        off  = 10
                    if len(raw) < off + flen:
                        break
                    frame = raw[off:off + flen]
                    raw   = raw[off + flen:]
                    try:
                        msg    = json.loads(frame.decode("utf-8"))
                        msg_id = msg.get("id")
                        if msg_id and msg_id in self._pending:
                            entry = self._pending[msg_id]
                            entry["value"] = (
                                msg.get("result", {}).get("result", {}).get("value")
                            )
                            entry["event"].set()
                    except Exception:
                        pass
            except socket.timeout:
                continue
            except Exception:
                break
        # Socket dropped
        with self._lock:
            if my_sock is self._sock:
                self._sock = None
        # Wake all pending callers with None
        for entry in self._pending.values():
            entry["event"].set()
        log("CdpSession: connection dropped", level="DEBUG")

    @staticmethod
    def _send_frame(sock: socket.socket, data: bytes) -> None:
        mask   = b"\x05\x06\x07\x08"
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        plen   = len(data)
        if plen <= 125:
            header = struct.pack("!BB", 0x81, 0x80 | plen) + mask
        elif plen <= 65535:
            header = struct.pack("!BBH", 0x81, 0xFE, plen) + mask
        else:
            header = struct.pack("!BBQ", 0x81, 0xFF, plen) + mask
        sock.sendall(header + masked)

    # ── Public API ────────────────────────────────────────────────────────────

    def evaluate(self, expression: str, timeout: float = 8.0):
        """
        Execute a JS expression in the connected page and return its value.
        Reconnects automatically on a dropped connection (up to 3 attempts).
        Returns None on error or timeout.
        """
        for attempt in range(3):
            try:
                with self._lock:
                    self._ensure_connected()
                    sock   = self._sock
                    msg_id = self._next_id
                    self._next_id += 1
                    entry  = {"event": threading.Event(), "value": None}
                    self._pending[msg_id] = entry

                payload = json.dumps({
                    "id": msg_id, "method": "Runtime.evaluate",
                    "params": {
                        "expression":    expression,
                        "returnByValue": True,
                        "awaitPromise":  False,
                    },
                }).encode("utf-8")
                self._send_frame(sock, payload)

                if entry["event"].wait(timeout):
                    return self._pending.pop(msg_id, {}).get("value")
                self._pending.pop(msg_id, None)
                return None

            except Exception as exc:
                log(f"CdpSession.evaluate (attempt {attempt + 1}): {exc}", level="DEBUG")
                with self._lock:
                    self._sock = None
                time.sleep(min(2 ** attempt, 5))
        return None

    def navigate_settings(self, screen: str = "Models") -> None:
        """Navigate the Settings target to a specific screen via history.pushState."""
        self.evaluate(f"history.pushState({{}}, '', '/?settingsScreen={screen}')")

    def get_innertext(self) -> str:
        val = self.evaluate("document.documentElement.innerText")
        return val if isinstance(val, str) else ""

    def is_alive(self) -> bool:
        """Quick health check — returns True if the connection is working."""
        v = self.evaluate("1")
        return v == 1 or v == "1" or v is True

    def close(self) -> None:
        self._closed = True
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None


# ─────────────────────────────────────────────────────────────────────────────
# Session registry — singleton CdpSession for the settingsScreen target
# ─────────────────────────────────────────────────────────────────────────────

_settings_session: Optional[CdpSession] = None
_session_lock = threading.Lock()


def get_settings_session() -> Optional[CdpSession]:
    """
    Return (or create) the persistent CdpSession for the settingsScreen target.
    Returns None if no Settings target exists in the current CDP instance.
    """
    global _settings_session
    with _session_lock:
        # Validate existing session
        if _settings_session is not None:
            if not _settings_session._closed and _settings_session.is_alive():
                return _settings_session
            _settings_session.close()
            _settings_session = None

        # Find the Settings target and open a new session
        target = find_settings_target()
        if target:
            ws_url = target.get("webSocketDebuggerUrl")
            if ws_url:
                _settings_session = CdpSession(ws_url)
                log("CdpSession: new session for settingsScreen", level="DEBUG")
                return _settings_session
    return None


def invalidate_settings_session() -> None:
    """Called when Antigravity exits — forces a fresh session on next access."""
    global _settings_session
    with _session_lock:
        if _settings_session:
            _settings_session.close()
            _settings_session = None


def ensure_settings_open(port: int = CDP_PORT) -> Optional[CdpSession]:
    """
    Ensure the Settings > Models panel is open and return a CdpSession.

    If the settingsScreen CDP target already exists, returns a session to it.
    If not, tries to navigate any editor page to the Settings URL via
    history.pushState, waits up to 5 s for the target to appear, then
    returns a session.  Returns None if all attempts fail.

    Used by the launch and post_close triggers which need to open Settings
    programmatically when the user may not have it open.
    """
    # Fast path: settings already open
    existing = get_settings_session()
    if existing:
        return existing

    log("  Settings target not found — attempting to open via CDP...", level="DEBUG")

    # Find any editor page to navigate
    pages = _get_all_page_targets(port)
    editor = next(
        (t for t in pages if "settingsScreen" not in t.get("url", "")), None
    )
    if not editor:
        log("  No editor pages available to open Settings", level="WARN")
        return None

    # Navigate the editor page to the Settings URL
    cdp_evaluate(editor, "history.pushState({}, '', '/?settingsScreen=Models')")
    log("  Sent pushState to open Settings > Models", level="DEBUG")

    # Wait up to 6 s for the settingsScreen target to appear
    for _ in range(6):
        time.sleep(1.0)
        target = find_settings_target(port)
        if target:
            ws_url = target.get("webSocketDebuggerUrl")
            if ws_url:
                global _settings_session
                with _session_lock:
                    if _settings_session:
                        _settings_session.close()
                    _settings_session = CdpSession(ws_url)
                log("  Settings target opened successfully", level="DEBUG")
                return _settings_session

    log("  Could not open Settings > Models within timeout", level="DEBUG")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Silent window minimisation (best-effort)
# ─────────────────────────────────────────────────────────────────────────────

def try_minimize_settings(target: dict) -> bool:
    """
    Attempt to minimise the Settings window via CDP Browser.setWindowBounds.

    This works only if Antigravity's Electron build exposes the Browser domain.
    If it doesn't, the window will be briefly visible — that is expected and
    documented behaviour.  Returns True if minimised, False otherwise.
    """
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        return False
    try:
        m    = re.match(r"ws://([^/:]+):?(\d+)?(/.*)?", ws_url)
        host = m.group(1)
        port = int(m.group(2) or 80)
        path = m.group(3) or "/"
        key  = base64.b64encode(b"AntigravityMinimize").decode()
        hs   = (
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        sock = socket.create_connection((host, port), timeout=5)
        sock.sendall(hs.encode())
        buf  = b""
        while b"\r\n\r\n" not in buf:
            buf += sock.recv(4096)

        def _send(d: dict) -> None:
            data   = json.dumps(d).encode("utf-8")
            mask   = b"\x01\x02\x03\x04"
            masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            plen   = len(data)
            hdr    = (struct.pack("!BBH", 0x81, 0xFE, plen) if plen > 125
                      else struct.pack("!BB", 0x81, 0x80 | plen)) + mask
            sock.sendall(hdr + masked)

        def _recv(t: float = 3.0) -> dict:
            sock.settimeout(t)
            raw = b""
            dl  = time.time() + t
            while time.time() < dl:
                try:
                    raw += sock.recv(65536)
                except socket.timeout:
                    break
                if len(raw) < 2:
                    continue
                b1   = raw[1]
                flen = b1 & 0x7F
                off  = 2
                if flen == 126:
                    if len(raw) < 4:
                        continue
                    flen = struct.unpack("!H", raw[2:4])[0]
                    off  = 4
                if len(raw) >= off + flen:
                    return json.loads(raw[off:off + flen].decode())
            return {}

        _send({"id": 1, "method": "Browser.getWindowForTarget"})
        resp      = _recv()
        window_id = resp.get("result", {}).get("windowId")
        if window_id is None:
            sock.close()
            return False

        _send({"id": 2, "method": "Browser.setWindowBounds",
               "params": {"windowId": window_id, "bounds": {"windowState": "minimized"}}})
        _recv()
        sock.close()
        log("Settings window minimised via Browser.setWindowBounds", level="DEBUG")
        return True
    except Exception as exc:
        log(f"Cannot minimise window (Browser domain not exposed): {exc}", level="WARN")
        log("Settings panel may be briefly visible — expected on some Electron builds.", level="WARN")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# JS expressions
# ─────────────────────────────────────────────────────────────────────────────

# Cheap trigger: runs every POLL_INTERVAL on each editor target.
# Returns 'profile_menu' | 'sign_out_dialog' | null.
# Single querySelector walk — significantly cheaper than a full innerText read.
_CHEAP_TRIGGER_JS = r"""
(function() {
    // 1. Profile menu: look for a 'Sign Out' interactive element.
    //    Using querySelectorAll then .find() — much cheaper than spreading all nodes.
    var els = document.querySelectorAll(
        'button, li, a, [role="menuitem"], [role="option"], [role="listitem"]'
    );
    for (var i = 0; i < els.length; i++) {
        if (els[i].innerText && els[i].innerText.trim() === 'Sign Out') {
            return 'profile_menu';
        }
    }
    // 2. Sign-out confirmation dialog: targeted text check on body.
    //    The dialog is a workbench-level modal rendered in the editor page DOM.
    if (document.body && document.body.innerText.includes('Sign out of')) {
        return 'sign_out_dialog';
    }
    return null;
})()
"""

# Observer injection: installed into the settingsScreen target on EVERY new connection.
# Uses a timestamp (Date.now()) so the watcher can detect CHANGES, not just presence.
# The guard window.__quotaObserverInstalled prevents double-injection on same page load.
_OBSERVER_JS = r"""
(function() {
    if (window.__quotaObserverInstalled) return 'already';
    window.__quotaObserverInstalled = true;
    window.__quotaUpdated = 0;
    var observer = new MutationObserver(function() {
        var text = document.body.innerText;
        if (text.indexOf('Claude and GPT models') !== -1 && text.indexOf('%') !== -1) {
            window.__quotaUpdated = Date.now();
        }
    });
    observer.observe(document.body, {
        subtree: true,
        childList: true,
        characterData: true
    });
    return 'installed';
})()
"""

# Read and reset the mutation timestamp (returns integer ms, or 0 if not triggered).
_MUTATION_FLAG_JS = "(function(){ var v = window.__quotaUpdated || 0; return v; })()"

# Click the last Refresh button (= Models section).
_REFRESH_JS = r"""
(function() {
    var btns = Array.from(document.querySelectorAll('button'))
        .filter(function(b) { return b.innerText.trim() === 'Refresh'; });
    if (btns.length) { btns[btns.length - 1].click(); return true; }
    return false;
})()
"""

# Cheap short-text query for trigger detection on editor pages.
# Returns first 2000 chars of body text — enough to detect profile menu / dialog.
_SHORT_TEXT_JS = "document.body ? document.body.innerText.slice(0, 2000) : ''"

# Comprehensive trigger-detection JS for the main workbench window.
# Returns 'profile_menu', 'sign_out_dialog', or null.
# Checks multiple string patterns so it works regardless of Antigravity version.
_TRIGGER_DETECT_JS = r"""
(function() {
    if (!document.body) return null;
    var body = document.body;
    var text = body.innerText || '';

    // ── Profile menu: check interactive elements for 'Sign Out' ──────────
    // This appears in the profile dropdown when the user clicks the avatar.
    var selectors = 'button, li, a, [role="menuitem"], [role="option"], .action-label';
    var els = body.querySelectorAll(selectors);
    for (var i = 0; i < els.length; i++) {
        var t = (els[i].innerText || '').trim();
        if (t === 'Sign Out' || t === 'Sign out') return 'profile_menu';
    }

    // Also check body text for unique profile-menu strings
    if (text.indexOf('Manage Trusted Extensions') > -1) return 'profile_menu';
    if (text.indexOf('Turn on Cloud Changes') > -1)    return 'profile_menu';
    if (text.indexOf('Export Profile')         > -1)   return 'profile_menu';
    if (text.indexOf('Import Profile')         > -1)   return 'profile_menu';

    // ── Sign-out confirmation dialog ──────────────────────────────────────
    if (text.indexOf('Sign out of') > -1)               return 'sign_out_dialog';
    if (text.indexOf('Do you want to sign out') > -1)   return 'sign_out_dialog';
    if (text.indexOf('All local changes will be lost') > -1) return 'sign_out_dialog';

    return null;
})()
"""

# Email extraction expressions (run on settingsScreen/Account page)
_EMAIL_REGEX_JS = r"""
(function() {
    var text    = document.documentElement.innerText;
    var matches = Array.from(new Set(
        (text.match(/[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}/g) || [])
    ));
    return matches.find(function(m) {
        return m.length < 100 && m.indexOf('.') > m.indexOf('@');
    }) || null;
})()
"""

_EMAIL_LEAF_JS = r"""
(function() {
    var last = null;
    document.querySelectorAll('*').forEach(function(el) {
        if (el.children.length === 0 && el.innerText &&
            el.innerText.includes('@') && el.innerText.includes('.')) {
            last = el.innerText.trim();
        }
    });
    return last;
})()
"""


# ─────────────────────────────────────────────────────────────────────────────
# Quota parser
# ─────────────────────────────────────────────────────────────────────────────

def _find_line(lines: list, keyword: str) -> Optional[int]:
    kw = keyword.lower()
    for i, l in enumerate(lines):
        if kw in l.lower():
            return i
    return None


def _find_pct(lines: list, from_i: int, to_i: int) -> Optional[int]:
    for line in lines[from_i:to_i]:
        m = re.search(r"\b(\d{1,3})\s*%", line)
        if m:
            v = int(m.group(1))
            if 0 <= v <= 100:
                return v
    return None


def _find_reset(lines: list, from_i: int, to_i: int) -> Optional[str]:
    pat = re.compile(
        r"(\d+\s+days?(?:[^\.\n]*)?"
        r"|\d+\s+hours?(?:[^\.\n]*)?"
        r"|\d+\s+minutes?(?:[^\.\n]*)?)",
        re.IGNORECASE,
    )
    for line in lines[from_i:to_i]:
        m = pat.search(line)
        if m:
            return m.group(0).strip()
    return None


def _extract_number(text: str, unit: str) -> int:
    m = re.search(rf"(\d+)\s+{unit}s?", text, re.IGNORECASE)
    return int(m.group(1)) if m else 0


def parse_reset_to_timestamp(raw: Optional[str],
                             captured_at: datetime.datetime) -> Optional[str]:
    if not raw:
        return None
    delta = datetime.timedelta(
        days    = _extract_number(raw, "day"),
        hours   = _extract_number(raw, "hour"),
        minutes = _extract_number(raw, "minute"),
    )
    return (captured_at + delta).isoformat() if delta.total_seconds() > 0 else None


def _parse_section(lines: list, start: int, end: int,
                   section_name: str = "unknown") -> Optional[dict]:
    """
    Parse a quota section (e.g. 'claudeGpt' or 'gemini') from a slice of
    Settings > Models innerText lines.

    On parse failure, logs which specific label search failed so UI drift
    produces an actionable bug report rather than a silent None return.
    """
    section = lines[start:end]
    wi = _find_line(section, "Weekly Limit")
    fi = _find_line(section, "Five Hour Limit")

    # ── Specific label failure logging ──────────────────────────────────────
    if wi is None:
        log(
            f"  Parse [{section_name}]: 'Weekly Limit' label NOT FOUND in "
            f"section lines [{start}:{end}] ({len(section)} lines). "
            "This string may have changed in a recent Antigravity update — "
            "use 'Run Diagnostics' from the tray menu and paste the output into a GitHub issue.",
            level="WARN",
        )
        return None
    if fi is None:
        log(
            f"  Parse [{section_name}]: 'Five Hour Limit' label NOT FOUND in "
            f"section lines [{start}:{end}] ({len(section)} lines). "
            "This string may have changed in a recent Antigravity update — "
            "use 'Run Diagnostics' from the tray menu and paste the output into a GitHub issue.",
            level="WARN",
        )
        return None

    wp = _find_pct(section, wi, fi)
    wr = _find_reset(section, wi, fi)
    fp = _find_pct(section, fi, len(section))
    fr = _find_reset(section, fi, len(section))

    # ── Percentage extraction failure logging ────────────────────────────────
    if wp is None:
        log(
            f"  Parse [{section_name}]: weeklyPct NOT FOUND between "
            f"'Weekly Limit' (line {wi}) and 'Five Hour Limit' (line {fi}). "
            f"Lines searched: {section[wi:fi]!r}",
            level="WARN",
        )
    if fp is None:
        log(
            f"  Parse [{section_name}]: fiveHourPct NOT FOUND after "
            f"'Five Hour Limit' (line {fi}) to end of section (line {len(section)}). "
            f"Lines searched: {section[fi:]!r}",
            level="WARN",
        )
    if wp is None or fp is None:
        return None

    if wr and _extract_number(wr, "day")  > 7: return None
    if fr and _extract_number(fr, "hour") > 5: return None
    return {"weeklyPct": wp, "weeklyReset": wr, "fiveHourPct": fp, "fiveHourReset": fr}


def parse_quota(text: str) -> Optional[dict]:
    """Parse the Settings > Models innerText into structured quota data."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    gi    = _find_line(lines, "Gemini Models")
    ci    = _find_line(lines, "Claude and GPT models")

    if ci is None:
        log(
            "  Parse: 'Claude and GPT models' section header NOT FOUND in Settings > Models text. "
            f"Total lines in text: {len(lines)}. "
            "This is likely a UI change in Antigravity — "
            "use 'Run Diagnostics' from the tray menu and paste the output into a GitHub issue.",
            level="WARN",
        )
        return None

    if gi is None:
        log(
            "  Parse: 'Gemini Models' section header not found — skipping Gemini section.",
            level="DEBUG",
        )

    return {
        "gemini":    _parse_section(lines, gi, ci, "gemini") if gi is not None else None,
        "claudeGpt": _parse_section(lines, ci, len(lines), "claudeGpt"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard POST helpers
# ─────────────────────────────────────────────────────────────────────────────

def post_reading(email: str, quota: dict,
                 captured_at: datetime.datetime, trigger: str) -> bool:
    """POST a quota reading to the dashboard."""
    if DRY_RUN:
        log(f"[DRY-RUN] Would POST reading for {email} (trigger={trigger})")
        return True
    # Attach computed reset timestamps
    for key in ("gemini", "claudeGpt"):
        sec = (quota or {}).get(key)
        if sec:
            sec["weeklyResetAt"]   = parse_reset_to_timestamp(sec.get("weeklyReset"), captured_at)
            sec["fiveHourResetAt"] = parse_reset_to_timestamp(sec.get("fiveHourReset"), captured_at)
    payload = {
        "accountId":  email,
        "capturedAt": captured_at.isoformat(),
        "trigger":    trigger,
        "quota":      quota,
    }
    ok = _http_post_json(f"{DASHBOARD_URL}/api/readings", payload)
    if ok:
        log(f"  -> POST OK  ({email}  trigger={trigger})")
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# Heartbeat
# ─────────────────────────────────────────────────────────────────────────────

_hb: dict = {
    "trigger_count":  0,
    "last_capture_at": None,
    "last_trigger":   None,
}


def post_heartbeat(status: str = "live") -> None:
    if DRY_RUN:
        return
    _http_post_json(
        f"{DASHBOARD_URL}/api/heartbeat",
        {
            "status":        status,
            "lastCaptureAt": _hb["last_capture_at"],
            "triggerCount":  _hb["trigger_count"],
            "lastTrigger":   _hb["last_trigger"],
            "version":       "3.0.0",
        },
        timeout=4.0,
    )


def _heartbeat_loop() -> None:
    while True:
        try:
            post_heartbeat()
        except Exception:
            pass
        time.sleep(HEARTBEAT_INTERVAL)


# ─────────────────────────────────────────────────────────────────────────────
# Email reader (via settingsScreen Account page)
# ─────────────────────────────────────────────────────────────────────────────

def read_email(sess: CdpSession) -> Optional[str]:
    """
    Navigate the Settings target to the Account screen, extract the email,
    then navigate back to Models.  Returns the email string or None.
    """
    sess.navigate_settings("Account")
    time.sleep(1.5)

    # Primary: regex scan over full innerText
    raw   = sess.evaluate(_EMAIL_REGEX_JS)
    email = raw if (raw and _EMAIL_RE.match(str(raw))) else None

    # Fallback: leaf-node scan (confirmed working in test-e.js)
    if not email:
        raw   = sess.evaluate(_EMAIL_LEAF_JS)
        email = raw if (raw and _EMAIL_RE.match(str(raw))) else None

    log(f"  Email: {email!r}", level="DEBUG")
    return email


# ─────────────────────────────────────────────────────────────────────────────
# Core capture sequence
# ─────────────────────────────────────────────────────────────────────────────

_capture_state = {
    "last_capture_ts": 0.0,   # time.time() of last successful capture (for debounce)
}

# Bug B fix: single non-blocking lock — only ONE capture thread may run at a time.
# _fire_capture() calls acquire(blocking=False); if it fails it logs + returns.
_capture_lock = threading.Lock()


def run_capture_sequence(trigger: str, needs_refresh: bool = True,
                         session: Optional[CdpSession] = None) -> None:
    """
    Full capture: read email + quota, POST to dashboard, fire toast.

    Args
    ────
    trigger        One of: launch | profile_menu | sign_out_dialog |
                            manual_refresh | post_close
    needs_refresh  True  → click Refresh and wait 3 s before reading quota.
                   False → data is already fresh (manual_refresh trigger only).
    session        Pre-existing CdpSession (for post_close relaunch).
                   If None, one is obtained from get_settings_session().
    """
    # Lock is already held by our caller (_fire_capture); nothing to set here.
    captured_at = datetime.datetime.now()
    if _HAS_APP_STATE and _app_state:
        _app_state.set_capturing(True, trigger)
    log(f"== Capture [{trigger}] started ==================================")
    try:
        # 1. Acquire Settings session.
        #    For launch / post_close triggers the Settings panel may not be open
        #    yet, so ensure_settings_open() tries to open it via CDP.
        sess = session or ensure_settings_open()
        if sess is None:
            if trigger in ("launch", "ghost_settings"):
                # At launch time the Settings panel is not yet open — this is
                # normal behaviour, not an error.  The safety-net / profile-menu
                # / sign-out triggers will capture once the user opens Settings.
                log(
                    f"  [{trigger}] Settings panel not open yet — skipping. "
                    "Will capture automatically when Settings is opened.",
                    level="INFO",
                )
            else:
                log("  Settings > Models could not be opened", level="ERROR")
                toast("Capture failed: could not open Settings > Models")
            return

        # 2. Read email from Account page
        email = read_email(sess)
        if not email:
            log("  Email not found on Account page", level="ERROR")
            toast("Capture failed: could not read email from Settings > Account")
            return
        log(f"  Email: {email}")

        # 3. Return to Models page
        sess.navigate_settings("Models")
        time.sleep(0.5)

        # 4. Click Refresh (all triggers except manual_refresh)
        if needs_refresh:
            clicked = sess.evaluate(_REFRESH_JS)
            log(f"  Refresh clicked: {clicked}  (waiting 3 s for fresh data...)")
            time.sleep(3.0)
        else:
            log("  Skipping Refresh — data already fresh from manual click")

        # 5. Read and parse quota
        text  = sess.get_innertext()
        quota = parse_quota(text)
        if not quota:
            log("  Parse failed — is Settings > Models fully loaded?", level="ERROR")
            toast(f"Capture failed: could not parse quota for {email}")
            return

        cg  = quota.get("claudeGpt") or {}
        gem = quota.get("gemini")    or {}
        log(f"  Claude/GPT   weekly={cg.get('weeklyPct')}%   5hr={cg.get('fiveHourPct')}%")
        log(f"  Gemini       weekly={gem.get('weeklyPct')}%   5hr={gem.get('fiveHourPct')}%")

        # 6. POST to dashboard
        ok = post_reading(email, quota, captured_at, trigger)

        # 7. Update heartbeat stats
        _hb["trigger_count"]   += 1
        _hb["last_capture_at"]  = captured_at.isoformat()
        _hb["last_trigger"]     = trigger
        _capture_state["last_capture_ts"] = time.time()

        # 8. Update shared state
        if _HAS_APP_STATE and _app_state:
            _app_state.set_capture_complete(trigger, email, ok)
            try:
                from server.db import list_accounts_with_latest
                _app_state.set_accounts(list_accounts_with_latest())
            except Exception:
                pass

        # 9. Toast summary
        lines = []
        if cg:
            lines.append(f"Claude/GPT:  {cg.get('weeklyPct')}% weekly / {cg.get('fiveHourPct')}% 5hr")
        if gem:
            lines.append(f"Gemini:      {gem.get('weeklyPct')}% weekly / {gem.get('fiveHourPct')}% 5hr")
        if not ok:
            lines.append("(!) Dashboard POST failed")
        toast(
            "\n".join(lines) or "Quota captured successfully",
            title=f"[{trigger}] Quota saved — {email}",
        )

    except Exception as exc:
        log(f"  Capture error: {exc}", level="ERROR")
        toast(f"Capture error ({trigger}): {exc}")
        if _HAS_APP_STATE and _app_state:
            _app_state.set_capture_error(trigger, str(exc))
    finally:
        # _capture_lock is released by the wrapper in _fire_capture().
        log(f"== Capture [{trigger}] finished =================================")


def _fire_capture(trigger: str, needs_refresh: bool = True) -> None:
    """
    Debounce guard + single-capture-at-a-time enforcement, then launch in a
    daemon thread.

    Bug A fix: debounce uses time.time() delta (unchanged).
    Bug B fix: _capture_lock.acquire(blocking=False) ensures only one capture
               thread runs at a time.  A second concurrent trigger is logged
               and dropped — not queued — so the watcher stays responsive.
    """
    elapsed = time.time() - _capture_state["last_capture_ts"]
    if elapsed < DEBOUNCE:
        log(f"Trigger '{trigger}' debounced ({elapsed:.0f}s < {DEBOUNCE}s)", level="DEBUG")
        return
    if not _capture_lock.acquire(blocking=False):
        log(f"Capture already in progress — skipping '{trigger}' trigger")
        return
    # Lock acquired here; released in the thread's finally block.
    def _run():
        try:
            run_capture_sequence(trigger=trigger, needs_refresh=needs_refresh)
        finally:
            _capture_lock.release()
    threading.Thread(target=_run, daemon=True, name=f"Capture-{trigger}").start()


def fire_capture(trigger: str = "manual_tray", needs_refresh: bool = True) -> None:
    """
    Public wrapper around _fire_capture() for external callers
    (tray popup, tray menu, main.py).

    trigger       Identifier shown in the activity log.
    needs_refresh True = click the Refresh button before reading quota.
                  All five CDP triggers except manual_refresh use True.
    """
    _fire_capture(trigger, needs_refresh)


# ─────────────────────────────────────────────────────────────────────────────
# Process monitor — psutil-based (launch + post-close triggers)
# ─────────────────────────────────────────────────────────────────────────────

# Names / exe substrings that identify the Antigravity process.
_AG_NAMES = ["Antigravity IDE", "Antigravity", "antigravity"]

# Handle for the process we relaunched ourselves (excluded from detection).
_relaunch_proc: Optional[subprocess.Popen] = None


def find_antigravity_process() -> Optional["psutil.Process"]:  # type: ignore[name-defined]
    """Return the first psutil.Process matching Antigravity, or None."""
    if not _HAS_PSUTIL:
        return None
    try:
        for proc in _psutil.process_iter(["pid", "name", "exe"]):
            name = proc.info.get("name") or ""
            exe  = proc.info.get("exe")  or ""
            if any(ag in name or ag in exe for ag in _AG_NAMES):
                if _relaunch_proc and proc.pid == _relaunch_proc.pid:
                    continue   # skip our own relaunch
                return proc
    except (_psutil.NoSuchProcess, _psutil.AccessDenied):
        pass
    return None


def _get_exe_and_args(proc: "psutil.Process") -> tuple:  # type: ignore[name-defined]
    try:
        cmdline = proc.cmdline()
        if cmdline:
            return cmdline[0], cmdline[1:]
    except (_psutil.NoSuchProcess, _psutil.AccessDenied):
        pass
    try:
        return proc.exe(), []
    except (_psutil.NoSuchProcess, _psutil.AccessDenied):
        return "", []


def _wait_for_cdp(port: int = CDP_PORT, timeout: float = RELAUNCH_TIMEOUT) -> bool:
    """Poll until CDP is reachable. Returns True on success."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _http_get_json(f"http://localhost:{port}/json", timeout=2.0)
            return True
        except Exception:
            time.sleep(1.0)
    return False


def _open_settings_after_relaunch(port: int = CDP_PORT) -> Optional[CdpSession]:
    """
    After relaunching Antigravity, navigate to Settings > Models and return
    a CdpSession.  Falls back gracefully if navigation fails.
    """
    # Wait up to 20 s for page targets to appear
    targets  = []
    deadline = time.time() + 20
    while time.time() < deadline:
        targets = _get_all_page_targets(port)
        if targets:
            break
        time.sleep(1)

    if not targets:
        log("  Relaunch: no CDP page targets appeared", level="WARN")
        return None

    # If settingsScreen already opened automatically, use it directly
    settings_t = find_settings_target(port)
    if settings_t:
        ws_url = settings_t.get("webSocketDebuggerUrl")
        if ws_url:
            return CdpSession(ws_url)

    # Try navigating an editor target to the Settings URL
    targets = _get_all_page_targets(CDP_PORT)
    # Use the main editor window (not the Settings panel) to push the URL change
    editor = next((t for t in targets if not _is_settings_panel(t)), None)
    if editor:
        cdp_evaluate(editor, "history.pushState({}, '', '/?settingsScreen=Models')")
        time.sleep(2)
        settings_t = find_settings_target(port)
        if settings_t:
            ws_url = settings_t.get("webSocketDebuggerUrl")
            if ws_url:
                return CdpSession(ws_url)

    log("  Relaunch: could not open Settings > Models — capture skipped", level="WARN")
    return None


def relaunch_and_capture(exe_path: str, original_args: list) -> None:
    """
    Post-close trigger: briefly relaunch Antigravity to capture the final
    quota snapshot, then terminate the relaunched instance.

    Flow
    ────
    1. Append --remote-debugging-port if not already present in args.
    2. Launch the process (minimised window if possible on Windows).
    3. Wait for CDP to become available (up to RELAUNCH_TIMEOUT s).
    4. Minimise the Settings window via Browser.setWindowBounds (best-effort).
    5. Navigate to Settings > Models, run capture sequence.
    6. Terminate the relaunched process.
    """
    global _relaunch_proc
    log("Post-close trigger: relaunching Antigravity for final quota capture...")
    log(
        "NOTE: The Settings panel may flash briefly. See README.md § Known Limitations.",
        level="INFO",
    )

    args     = list(original_args)
    cdp_flag = f"--remote-debugging-port={CDP_PORT}"
    if not any("remote-debugging-port" in a for a in args):
        args.append(cdp_flag)

    try:
        # Launch — suppress console window on Windows
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        _relaunch_proc = subprocess.Popen([exe_path] + args, **kwargs)
        log(f"  Relaunched: PID {_relaunch_proc.pid}", level="DEBUG")

        if not _wait_for_cdp(CDP_PORT, RELAUNCH_TIMEOUT):
            log("  CDP did not become available within timeout", level="ERROR")
            return
        log("  CDP available after relaunch")
        time.sleep(RELAUNCH_SETTLE)

        # Minimise window (best-effort)
        settings_t = find_settings_target()
        if settings_t:
            try_minimize_settings(settings_t)

        # Navigate and capture
        sess = _open_settings_after_relaunch()
        if sess:
            run_capture_sequence("post_close", needs_refresh=True, session=sess)
            sess.close()
        else:
            log("  Post-close capture: could not get Settings session", level="ERROR")
            toast("Post-close capture failed: could not open Settings > Models")

    except Exception as exc:
        log(f"  Relaunch error: {exc}", level="ERROR")
    finally:
        if _relaunch_proc:
            try:
                _relaunch_proc.terminate()
                _relaunch_proc.wait(timeout=10)
                log("  Relaunched Antigravity terminated")
            except Exception:
                try:
                    _relaunch_proc.kill()
                except Exception:
                    pass
            _relaunch_proc = None


# ─────────────────────────────────────────────────────────────────────────────
# MutationObserver management
# ─────────────────────────────────────────────────────────────────────────────

def ensure_observer(sess: CdpSession) -> None:
    """
    Inject the MutationObserver into the settingsScreen target.
    Uses window.__quotaObserverInstalled guard to prevent double-injection.
    Called on EVERY new CDP connection (not just startup) so Settings page
    reloads don't lose the observer.
    """
    installed = sess.evaluate("!!window.__quotaObserverInstalled")
    if not installed:
        result = sess.evaluate(_OBSERVER_JS)
        log(f"MutationObserver: {result}", level="DEBUG")


# ─────────────────────────────────────────────────────────────────────────────
# Main polling loop (v4 — edge detection, safety net, notification on close)
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    mode = "DRY-RUN" if DRY_RUN else "LIVE"
    log(f"Antigravity Quota Tracker v3.0  [{mode}]")
    log(f"CDP={CDP_PORT}  poll={POLL_INTERVAL}s  debounce={DEBOUNCE}s  dashboard={DASHBOARD_URL}")
    log(f"Triggers: launch | profile_menu | sign_out_dialog | manual_refresh | safety_net({SAFETY_NET_INTERVAL}s)")
    if not _HAS_PSUTIL:
        log(
            "psutil not installed — launch and close triggers disabled. "
            "Run: pip install psutil",
            level="WARN",
        )
    log("")

    # Start heartbeat thread
    threading.Thread(target=_heartbeat_loop, daemon=True, name="Heartbeat").start()

    # ── State for edge detection ──────────────────────────────────────────────
    ag_was_running    = False
    ag_exe:    str    = ""
    ag_args:   list   = []

    # CDP-trigger edge state (all False = "was not visible")
    last_state = {
        "profile_menu_visible":    False,
        "sign_out_dialog_visible": False,
        "mutation_ts":             0,      # last seen __quotaUpdated timestamp
    }

    # Tracks the current settings CdpSession; None = need to (re)connect
    sess: Optional[CdpSession] = None
    sess_connected_for_pid: Optional[int] = None   # PID when sess was opened

    # Bug A fix: initialize to now so first safety-net fires SAFETY_NET_INTERVAL
    # seconds AFTER the watcher starts, not immediately.
    last_safety_net  = time.time()
    no_cdp_warned_at = 0.0

    # Bug C fix: persistent CdpSession pool for main workbench windows.
    # Keyed by CDP target id.  Cleaned up when targets disappear.
    main_sessions: dict = {}

    # Tracks whether there were main-editor CDP targets on the previous poll.
    # Used to detect "user closed main editor while Settings is still open".
    main_pages_had_targets: bool = False

    while True:
        try:
            # Skip polling while a capture lock is held (avoids concurrent DOM nav)
            if _capture_lock.locked():
                time.sleep(POLL_INTERVAL)
                continue

            # ── 1. Process detection ──────────────────────────────────────────
            ag_proc = find_antigravity_process()
            ag_running = ag_proc is not None
            ag_pid = ag_proc.pid if ag_proc else None

            # Launch edge: was not running → now running
            if ag_running and not ag_was_running:
                ag_exe, ag_args = _get_exe_and_args(ag_proc)
                log(f"Antigravity detected: PID={ag_pid}  exe={ag_exe[:60]!r}")
                no_cdp_warned_at = 0.0

                # Establish fresh CDP sessions for this new process
                sess = None
                sess_connected_for_pid = None
                # Close all stale main-window sessions from the previous process
                for _s in main_sessions.values():
                    try:
                        _s.close()
                    except Exception:
                        pass
                main_sessions.clear()
                # Bug A fix: reset safety-net so it doesn't immediately fire
                # after launch (the launch trigger already captures fresh data).
                last_safety_net = time.time()

                def _do_launch():
                    time.sleep(RELAUNCH_SETTLE)   # let CDP stabilise
                    _fire_capture("launch", needs_refresh=True)
                threading.Thread(target=_do_launch, daemon=True, name="Launch").start()

            # Close edge: was running → now stopped
            if not ag_running and ag_was_running:
                elapsed_since_capture = time.time() - _capture_state["last_capture_ts"]
                mins = int(elapsed_since_capture / 60)
                log(
                    f"Antigravity closed without sign-out trigger "
                    f"— last capture was {mins} minute(s) ago"
                )

                # ── Ghost-Settings capture ────────────────────────────────────
                # When the user closes Antigravity via X, the main editor window
                # closes but the Settings panel (a separate Electron renderer)
                # often survives — the user must close it manually.  Its CDP
                # target is still alive and reachable.
                #
                # Before giving up and asking the user to reopen, we check for
                # up to 5 s whether the Settings target is still alive.  If it
                # is, we run a full capture from it (Refresh → read → POST) with
                # no relaunch, no window flash, no user action needed.
                def _try_ghost_capture():
                    log("Checking for surviving Settings panel after editor close...")
                    surviving_sess = None
                    for attempt in range(5):
                        time.sleep(1.0)
                        target = find_settings_target(CDP_PORT)
                        if target:
                            ws_url = target.get("webSocketDebuggerUrl")
                            if ws_url:
                                try:
                                    surviving_sess = CdpSession(ws_url)
                                    log(
                                        f"Settings panel still alive after editor close "
                                        f"(found on attempt {attempt + 1}) — capturing final quota.",
                                    )
                                except Exception as e:
                                    log(f"  Ghost: could not connect to Settings panel: {e}", level="DEBUG")
                            break
                        log(f"  Ghost attempt {attempt + 1}/5: Settings target not found yet", level="DEBUG")

                    if surviving_sess:
                        if _capture_lock.acquire(blocking=False):
                            try:
                                run_capture_sequence(
                                    "ghost_settings",
                                    needs_refresh=True,
                                    session=surviving_sess,
                                )
                            finally:
                                _capture_lock.release()
                                try:
                                    surviving_sess.close()
                                except Exception:
                                    pass
                        else:
                            log("Ghost-settings: capture already in progress — skipping", level="DEBUG")
                            try:
                                surviving_sess.close()
                            except Exception:
                                pass
                        if _HAS_APP_STATE and _app_state:
                            _app_state.log(
                                "Antigravity closed — final quota captured from surviving Settings panel.",
                                _app_state.LEVEL_OK,
                            )
                    else:
                        # Settings panel is gone — fall back to old "please reopen" behaviour
                        log("Settings panel gone after editor close — final capture not possible", level="WARN")
                        toast(
                            "Antigravity closed — open it again to capture your final quota reading",
                            title="Antigravity Quota Tracker",
                        )
                        if _HAS_APP_STATE and _app_state:
                            _app_state.log(
                                "Antigravity closed — reopen to capture final reading",
                                _app_state.LEVEL_WARN,
                            )

                threading.Thread(target=_try_ghost_capture, daemon=True, name="GhostCapture").start()

                # Invalidate session immediately so the poll loop doesn't try
                # to use the dead CDP connection while GhostCapture is running.
                invalidate_settings_session()
                sess = None
                sess_connected_for_pid = None
                # Reset edge state and clear main-window sessions
                last_state["profile_menu_visible"]    = False
                last_state["sign_out_dialog_visible"] = False
                last_state["mutation_ts"]             = 0
                for _s in main_sessions.values():
                    try:
                        _s.close()
                    except Exception:
                        pass
                main_sessions.clear()


            ag_was_running = ag_running

            # ── 2. CDP trigger checks (only when Antigravity is running) ──────
            if not ag_running:
                if time.time() - no_cdp_warned_at > 60:
                    # Use check_cdp_port() to emit a specific, actionable message
                    # rather than a generic "not detected" warning.
                    port_status = check_cdp_port(CDP_PORT)
                    if port_status == "not_open":
                        log(
                            f"Antigravity process not found and port {CDP_PORT} is closed. "
                            "Launch Antigravity via the debug shortcut "
                            "(run scripts/setup-windows.ps1 once if you haven't already).",
                            level="WARN",
                        )
                    elif port_status == "conflict":
                        log(
                            f"Port {CDP_PORT} is open but is NOT responding with valid CDP JSON. "
                            "Something else is using this port. "
                            "Either stop the conflicting service, or change CDP_PORT in notifier/.env "
                            "and re-run the setup script with the matching --Port value.",
                            level="WARN",
                        )
                    else:
                        # Port is open and returning CDP data, but psutil can't see the process
                        # (rare: process may be running under a different name on this platform)
                        log(
                            f"CDP port {CDP_PORT} is responding but Antigravity process not detected by psutil. "
                            "Triggers will not fire until the process is found. "
                            "Verify the process name via Task Manager.",
                            level="WARN",
                        )
                    no_cdp_warned_at = time.time()
                time.sleep(POLL_INTERVAL)
                continue

            # ── 2a. Maintain/refresh CdpSession for settingsScreen ─────────────
            # Re-open if process changed or session dropped
            if sess is None or sess_connected_for_pid != ag_pid:
                sess = get_settings_session()
                if sess:
                    sess_connected_for_pid = ag_pid
                    # Always re-inject observer on new connection
                    ensure_observer(sess)
                    log("CDP session (re)connected and observer injected", level="DEBUG")

            # ── 2b. MutationObserver flag — manual_refresh trigger ─────────────
            if sess:
                mut_ts = sess.evaluate(_MUTATION_FLAG_JS)
                if isinstance(mut_ts, (int, float)) and mut_ts and mut_ts != last_state["mutation_ts"]:
                    log("Trigger: manual_refresh (MutationObserver detected % change)")
                    last_state["mutation_ts"] = mut_ts
                    _fire_capture("manual_refresh", needs_refresh=False)
                    time.sleep(POLL_INTERVAL)
                    continue

            # ── 2c. Main-window trigger poll (profile_menu / sign_out) ─────────────
            #
            # Root-cause fix: Antigravity sets ALL CDP page target URLs to
            # '?settingsScreen=...' (even the main editor).  We now use
            # _is_settings_panel() (title-based) to separate the floating Settings
            # panel from the main editor window(s).
            #
            # We run _TRIGGER_DETECT_JS on each main-editor target via persistent
            # CdpSession.  If the persistent session fails (returns None), we fall
            # back to one-shot cdp_evaluate so triggers still fire.

            pages = _get_all_page_targets(CDP_PORT)
            main_pages = [t for t in pages if not _is_settings_panel(t)]
            current_ids = {t.get("id") for t in main_pages}

            # ── Editor-closed CDP trigger ─────────────────────────────────────
            # Detect: main editor windows just disappeared while Settings panel
            # (and its CDP session) is still alive.
            #
            # This is the exact moment the user closed the editor via X — the
            # main renderer windows drop off the CDP /json list, but the Settings
            # panel renderer is still serving its CDP target.
            #
            # Fire a capture NOW (before the user closes Settings too).
            # This is fundamentally different from ghost capture:
            #   ghost_settings = fires AFTER psutil says process is dead (too late)
            #   editor_closed  = fires WHILE the process is still alive, at the
            #                    CDP level, using the still-open Settings panel.
            if main_pages_had_targets and not main_pages and sess is not None:
                if sess.is_alive():
                    log(
                        "Editor windows closed while Settings panel still alive "
                        "— capturing final quota now."
                    )
                    _fire_capture("editor_closed", needs_refresh=True)
            main_pages_had_targets = bool(main_pages)

            # Clean up sessions for targets that have disappeared
            for gone_id in [k for k in main_sessions if k not in current_ids]:
                try:
                    main_sessions[gone_id].close()
                except Exception:
                    pass
                del main_sessions[gone_id]

            profile_menu_now    = False
            sign_out_dialog_now = False

            if not main_pages:
                log("No main-editor CDP targets found (only Settings panel visible)",
                    level="DEBUG")

            for target in main_pages:
                target_id = target.get("id", "")
                ws_url    = target.get("webSocketDebuggerUrl", "")
                if not ws_url:
                    continue

                # Get or (re)create persistent session
                existing = main_sessions.get(target_id)
                if existing is None or existing._closed:
                    try:
                        main_sessions[target_id] = CdpSession(ws_url)
                        log(f"Main-window session connected id={target_id[:8]}",
                            level="DEBUG")
                    except Exception as exc:
                        log(f"Main-window connect failed: {exc}", level="DEBUG")

                trigger_result = None

                # Try persistent session first
                if target_id in main_sessions and not main_sessions[target_id]._closed:
                    try:
                        trigger_result = main_sessions[target_id].evaluate(_TRIGGER_DETECT_JS)
                    except Exception as exc:
                        log(f"Persistent session eval failed: {exc}", level="DEBUG")
                        main_sessions[target_id]._closed = True

                # Fallback: one-shot cdp_evaluate if persistent session failed
                if trigger_result is None:
                    trigger_result = cdp_evaluate(target, _TRIGGER_DETECT_JS, timeout=4.0)
                    if trigger_result:
                        log(f"  (used one-shot fallback — persistent session down)",
                            level="DEBUG")

                if trigger_result == "profile_menu":
                    profile_menu_now = True
                elif trigger_result == "sign_out_dialog":
                    sign_out_dialog_now = True

                if profile_menu_now or sign_out_dialog_now:
                    break

            # Edge: profile menu opened (fires ONCE per opening)
            if profile_menu_now and not last_state["profile_menu_visible"]:
                log("Trigger: profile_menu (dropdown opened in main window)")
                _fire_capture("profile_menu", needs_refresh=True)

            # Edge: sign-out dialog appeared
            if sign_out_dialog_now and not last_state["sign_out_dialog_visible"]:
                log("Trigger: sign_out_dialog")
                _fire_capture("sign_out_dialog", needs_refresh=True)

            last_state["profile_menu_visible"]    = profile_menu_now
            last_state["sign_out_dialog_visible"] = sign_out_dialog_now

            # ── 3. Safety-net timer ───────────────────────────────────────────
            if time.time() - last_safety_net > SAFETY_NET_INTERVAL:
                log("Trigger: safety_net (periodic capture)")
                _fire_capture("safety_net", needs_refresh=True)
                last_safety_net = time.time()

        except KeyboardInterrupt:
            raise
        except Exception as exc:
            log(f"Poll error: {exc}", level="ERROR")

        time.sleep(POLL_INTERVAL)


# ─────────────────────────────────────────────────────────────────────────────

def run_watcher() -> None:
    """
    Public entry point for the CDP watcher loop.
    Called in a daemon thread from main.py.
    """
    main()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Stopped by user.")
        sys.exit(0)
