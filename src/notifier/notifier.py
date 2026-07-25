#!/usr/bin/env python3
"""
notifier/notifier.py  —  Antigravity Quota Tracker v4.0

Two capture triggers
────────────────────
  1. LAUNCH       Antigravity process appears   → Refresh + read
  2. GetTurnDiff  Agent response completes      → Refresh + read

Architecture
────────────
  • CdpSession       — persistent stdlib WebSocket to the settingsScreen target.
                       Reconnects automatically when the connection drops.
                       One connection is maintained and reused for all quota reads.
  • NetworkListener  — persistent CDP WebSocket to the main workbench window.
                       Enables the Network domain and listens for requestWillBeSent
                       events.  Fires on_getturndiff() when GetTurnDiff appears in
                       the request URL.  A 500 ms debounce collapses the two rapid
                       GetTurnDiff calls emitted per agent response into one capture.
  • Heartbeat        — POST /api/heartbeat every 15 s so the dashboard can show a
                       live / stale / offline status dot.
  • Structured logging — levels DEBUG/INFO/WARN/ERROR, ASCII-safe terminal.

Configuration  (notifier/.env  or  environment variables)
──────────────
  CDP_PORT                  9222
  POLL_INTERVAL_SECONDS     2
  DEBOUNCE_SECONDS          2
  DASHBOARD_URL             http://localhost:4300
  DASHBOARD_API_KEY         (empty = open)
  LOG_LEVEL                 INFO   (DEBUG | INFO | WARN | ERROR)

Usage
─────
  python notifier/notifier.py              # live mode
  python notifier/notifier.py --dry-run   # log only, no POST / toasts
  python notifier/notifier.py --verbose   # DEBUG-level logging
"""

# ── stdlib ───────────────────────────────────────────────────────────────────
from __future__ import annotations
import sys, os, time, json, re, datetime, threading, subprocess
import urllib.request, urllib.error
import struct, socket, base64
from pathlib import Path
from typing import Optional, Callable

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

CDP_PORT           = _cfg("CDP_PORT",                 9222)
POLL_INTERVAL      = _cfg("POLL_INTERVAL_SECONDS",    2)
DEBOUNCE           = _cfg("DEBOUNCE_SECONDS",         2)   # between captures
DASHBOARD_URL      = _cfg("DASHBOARD_URL",            "http://localhost:4300")
DASHBOARD_API_KEY  = _cfg("DASHBOARD_API_KEY",        "")
LOG_LEVEL          = _cfg("LOG_LEVEL",                "INFO").upper()

HEARTBEAT_INTERVAL  = 15   # seconds between heartbeat POSTs
RELAUNCH_SETTLE     = 3    # seconds to wait for CDP to stabilise after launch
RELAUNCH_TIMEOUT    = 30   # kept for CDP wait helper (ensure_settings_open)

# GetTurnDiff fires exactly twice per completed agent response.
# 500 ms debounce collapses the pair into a single capture event.
GETTURNDIFF_DEBOUNCE = 0.5  # seconds

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
      'ok'       — port is open AND returning valid CDP JSON
      'conflict' — port is open but NOT returning valid CDP JSON
      'not_open' — connection refused (Antigravity not running with the flag)
    """
    try:
        data = _http_get_json(f"http://localhost:{port}/json", timeout=3.0)
        if not isinstance(data, list):
            return "conflict"
        if data and not any("webSocketDebuggerUrl" in t for t in data if isinstance(t, dict)):
            return "conflict"
        return "ok"
    except (OSError, ConnectionRefusedError):
        return "not_open"
    except Exception:
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
        if b1 & 0x80:  # masked frame
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

    Used by the launch trigger which needs to open Settings programmatically
    when the user may not have it open.
    """
    # Fast path: settings already open
    existing = get_settings_session()
    if existing:
        return existing

    log("  Settings target not found — attempting to open via CDP...", level="DEBUG")

    # Find any editor page to navigate
    pages = _get_all_page_targets(port)
    editor = next(
        (t for t in pages if not _is_settings_panel(t)), None
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
# NetworkListener — persistent CDP WebSocket to the main workbench window
# ─────────────────────────────────────────────────────────────────────────────

class NetworkListener:
    """
    Persistent CDP WebSocket connection to the main workbench window.

    Enables the Network domain and fires the provided callback whenever a
    Network.requestWillBeSent event contains 'GetTurnDiff' in the request URL.

    Design
    ──────
    • A single open socket is maintained.
    • Network.enable is sent immediately after the WebSocket handshake.
    • A background thread reads ALL incoming CDP frames (events and responses).
    • CDP events have a 'method' field (not 'id').  Only
      Network.requestWillBeSent events that include 'GetTurnDiff' in
      params.request.url trigger the callback.
    • close() stops the reader thread and closes the socket.

    Note: every agent response emits exactly two rapid GetTurnDiff requests.
    The caller is expected to debounce (see _on_getturndiff_event).
    """

    def __init__(self, ws_url: str, on_getturndiff: Callable[[], None]) -> None:
        self._ws_url        = ws_url
        self._on_getturndiff = on_getturndiff
        self._sock: Optional[socket.socket] = None
        self._closed        = False
        self._next_id       = 1
        self._connect()

    def _connect(self) -> None:
        m    = re.match(r"ws://([^/:]+):?(\d+)?(/.*)?", self._ws_url)
        host = m.group(1)
        port = int(m.group(2) or 80)
        path = m.group(3) or "/"
        key  = base64.b64encode(b"AntigravityNetListen").decode()
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
                raise ConnectionError("NetworkListener: WebSocket handshake failed")
            buf += chunk
        self._sock = sock

        # Enable Network domain — must be sent before events start flowing
        self._send_frame(
            sock,
            json.dumps({
                "id": self._next_id,
                "method": "Network.enable",
                "params": {},
            }).encode("utf-8"),
        )
        self._next_id += 1

        # Start the background reader thread
        threading.Thread(
            target=self._recv_loop, daemon=True, name="NetListener"
        ).start()
        log("NetworkListener: connected and Network domain enabled", level="DEBUG")

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

    def _recv_loop(self) -> None:
        """
        Background reader: parse all incoming CDP frames and dispatch events.

        CDP events do NOT have an 'id' field — they have 'method' and 'params'.
        We look specifically for Network.requestWillBeSent events that include
        'GetTurnDiff' in the request URL.
        """
        my_sock = self._sock
        raw     = b""
        while not self._closed and my_sock is self._sock:
            try:
                my_sock.settimeout(1.0)
                chunk = my_sock.recv(65536)
                if not chunk:
                    break
                raw += chunk
                # Consume all complete WebSocket frames in the buffer
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
                    if b1 & 0x80:   # server → client frames should not be masked,
                        off += 4    # but handle gracefully if they are
                    if len(raw) < off + flen:
                        break
                    frame = raw[off:off + flen]
                    raw   = raw[off + flen:]
                    try:
                        msg = json.loads(frame.decode("utf-8"))
                        # CDP events: 'method' present, no 'id'
                        if (msg.get("method") == "Network.requestWillBeSent"
                                and "id" not in msg):
                            url = (
                                msg.get("params", {})
                                   .get("request", {})
                                   .get("url", "")
                            )
                            if "GetTurnDiff" in url:
                                log(
                                    f"NetworkListener: GetTurnDiff → {url[:80]}",
                                    level="DEBUG",
                                )
                                self._on_getturndiff()
                    except Exception:
                        pass
            except socket.timeout:
                continue
            except Exception:
                break
        log("NetworkListener: connection dropped", level="DEBUG")

    def close(self) -> None:
        self._closed = True
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None


# ─────────────────────────────────────────────────────────────────────────────
# Network listener management (module-level singleton)
# ─────────────────────────────────────────────────────────────────────────────

_network_listener: Optional[NetworkListener] = None
_network_listener_lock = threading.Lock()
_last_getturndiff: float = 0.0


def _on_getturndiff_event() -> None:
    """
    Called on every GetTurnDiff network request.

    Antigravity fires exactly two rapid GetTurnDiff requests per completed
    agent response.  The 500 ms debounce collapses the pair into a single
    capture event so the dashboard receives one reading per response.
    """
    global _last_getturndiff
    now = time.time()
    if now - _last_getturndiff < GETTURNDIFF_DEBOUNCE:
        log(
            "GetTurnDiff: debounced (second fire of response pair — skipping)",
            level="DEBUG",
        )
        return
    _last_getturndiff = now
    log("Trigger: GetTurnDiff (agent response completed)")
    _fire_capture("GetTurnDiff", needs_refresh=True)


def setup_network_listener() -> Optional[NetworkListener]:
    """
    Connect to the main workbench window CDP target and enable the Network domain.

    Finds a non-Settings page target (the main workbench window), opens a
    persistent NetworkListener, and registers _on_getturndiff_event as the
    GetTurnDiff callback.

    Returns the NetworkListener on success, or None if no workbench target is
    found (e.g. Antigravity is still starting up — the caller will retry on the
    next poll cycle).
    """
    global _network_listener
    with _network_listener_lock:
        # Return existing listener if still alive
        if (_network_listener is not None
                and not _network_listener._closed
                and _network_listener._sock is not None):
            return _network_listener

        # Find the main workbench window (any page that is NOT the Settings panel)
        pages = _get_all_page_targets(CDP_PORT)
        workbench = next((t for t in pages if not _is_settings_panel(t)), None)
        if not workbench:
            log(
                "NetworkListener: no workbench target found — will retry",
                level="DEBUG",
            )
            return None

        ws_url = workbench.get("webSocketDebuggerUrl", "")
        if not ws_url:
            log("NetworkListener: workbench target has no webSocketDebuggerUrl", level="WARN")
            return None

        try:
            _network_listener = NetworkListener(ws_url, _on_getturndiff_event)
            log(
                f"NetworkListener: listening on {workbench.get('url', '')[:60]!r}"
            )
            return _network_listener
        except Exception as exc:
            log(f"NetworkListener: connection failed: {exc}", level="WARN")
            _network_listener = None
            return None


def teardown_network_listener() -> None:
    """Close and discard the persistent network listener (e.g. on process exit)."""
    global _network_listener
    with _network_listener_lock:
        if _network_listener:
            try:
                _network_listener.close()
            except Exception:
                pass
            _network_listener = None
    log("NetworkListener: torn down")


# ─────────────────────────────────────────────────────────────────────────────
# JS expressions (quota read only — trigger detection JS removed)
# ─────────────────────────────────────────────────────────────────────────────

# Click the last Refresh button (= Models section, not MCP).
_REFRESH_JS = r"""
(function() {
    var btns = Array.from(document.querySelectorAll('button'))
        .filter(function(b) { return b.innerText.trim() === 'Refresh'; });
    if (btns.length) { btns[btns.length - 1].click(); return true; }
    return false;
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
            "version":       "4.0.0",
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

# Single non-blocking lock — only ONE capture thread may run at a time.
# _fire_capture() calls acquire(blocking=False); if it fails it logs + returns.
_capture_lock = threading.Lock()


def run_capture_sequence(trigger: str, needs_refresh: bool = True,
                         session: Optional[CdpSession] = None) -> None:
    """
    Full capture: read email + quota, POST to dashboard, fire toast.

    Args
    ────
    trigger        One of: launch | GetTurnDiff | manual_tray
    needs_refresh  True  → click Refresh and wait 3 s before reading quota.
                   False → data is already fresh (unused in the two-trigger model,
                           retained for manual_tray compatibility).
    session        Pre-existing CdpSession (rarely needed). If None, one is
                   obtained from ensure_settings_open().
    """
    # Lock is already held by our caller (_fire_capture); nothing to set here.
    captured_at = datetime.datetime.now()
    if _HAS_APP_STATE and _app_state:
        _app_state.set_capturing(True, trigger)
    log(f"== Capture [{trigger}] started ==================================")
    try:
        # 1. Acquire Settings session.
        #    For the launch trigger the Settings panel may not be open yet,
        #    so ensure_settings_open() tries to open it via CDP.
        sess = session or ensure_settings_open()
        if sess is None:
            if trigger == "launch":
                # At launch time the Settings panel is not yet open — this is
                # normal behaviour.  The GetTurnDiff trigger will capture once
                # the user sends a message.
                log(
                    f"  [launch] Settings panel not open yet — skipping. "
                    "Will capture automatically on next agent response (GetTurnDiff).",
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

        # 4. Click Refresh and wait 3 s for fresh server-side data
        if needs_refresh:
            clicked = sess.evaluate(_REFRESH_JS)
            log(f"  Refresh clicked: {clicked}  (waiting 3 s for fresh data...)")
            time.sleep(3.0)
        else:
            log("  Skipping Refresh — data already fresh")

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

    Debounce uses time.time() delta against _capture_state["last_capture_ts"].
    _capture_lock.acquire(blocking=False) ensures only one capture thread runs
    at a time.  A second concurrent trigger is logged and dropped — not queued
    — so the watcher stays responsive.
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
    """
    _fire_capture(trigger, needs_refresh)


# ─────────────────────────────────────────────────────────────────────────────
# Process monitor — psutil-based (launch + close detection)
# ─────────────────────────────────────────────────────────────────────────────

# Names / exe substrings that identify the Antigravity process.
_AG_NAMES = ["Antigravity IDE", "Antigravity", "antigravity"]


def find_antigravity_process() -> Optional["psutil.Process"]:  # type: ignore[name-defined]
    """Return the first psutil.Process matching Antigravity, or None."""
    if not _HAS_PSUTIL:
        return None
    try:
        for proc in _psutil.process_iter(["pid", "name", "exe"]):
            name = proc.info.get("name") or ""
            exe  = proc.info.get("exe")  or ""
            if any(ag in name or ag in exe for ag in _AG_NAMES):
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


# ─────────────────────────────────────────────────────────────────────────────
# Main polling loop — two triggers: launch + GetTurnDiff
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    mode = "DRY-RUN" if DRY_RUN else "LIVE"
    log(f"Antigravity Quota Tracker v4.0  [{mode}]")
    log(f"CDP={CDP_PORT}  poll={POLL_INTERVAL}s  "
        f"debounce(captures)={DEBOUNCE}s  debounce(GetTurnDiff)={GETTURNDIFF_DEBOUNCE}s  "
        f"dashboard={DASHBOARD_URL}")
    log("Triggers: launch | GetTurnDiff")
    if not _HAS_PSUTIL:
        log(
            "psutil not installed — launch and close detection disabled. "
            "Run: pip install psutil",
            level="WARN",
        )
    log("")

    # Start heartbeat thread
    threading.Thread(target=_heartbeat_loop, daemon=True, name="Heartbeat").start()

    # ── Watcher state ─────────────────────────────────────────────────────────
    ag_was_running = False
    ag_exe: str    = ""
    ag_args: list  = []
    no_cdp_warned_at = 0.0

    while True:
        try:
            # ── 1. Process detection ──────────────────────────────────────────
            ag_proc    = find_antigravity_process()
            ag_running = ag_proc is not None
            ag_pid     = ag_proc.pid if ag_proc else None

            # Launch edge: was not running → now running
            if ag_running and not ag_was_running:
                ag_exe, ag_args = _get_exe_and_args(ag_proc)
                log(f"Antigravity detected: PID={ag_pid}  exe={ag_exe[:60]!r}")
                no_cdp_warned_at = 0.0

                # Invalidate any stale Settings session from a previous process
                invalidate_settings_session()

                def _do_launch():
                    time.sleep(RELAUNCH_SETTLE)  # let CDP stabilise
                    setup_network_listener()
                    _fire_capture("launch", needs_refresh=True)

                threading.Thread(target=_do_launch, daemon=True, name="Launch").start()

            # Close edge: was running → now stopped
            if not ag_running and ag_was_running:
                elapsed = time.time() - _capture_state["last_capture_ts"]
                mins    = int(elapsed / 60)
                log(f"Antigravity closed — last capture was {mins} minute(s) ago")
                toast(
                    f"Last quota captured {mins} min ago",
                    title="Antigravity closed",
                )
                teardown_network_listener()
                invalidate_settings_session()
                if _HAS_APP_STATE and _app_state:
                    _app_state.log(
                        "Antigravity closed — reopen to continue tracking",
                        _app_state.LEVEL_WARN,
                    )

            ag_was_running = ag_running

            # ── 2. CDP warnings when Antigravity is not running ───────────────
            if not ag_running:
                if time.time() - no_cdp_warned_at > 60:
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
                        # Port is open and returning CDP data but psutil can't find the process
                        log(
                            f"CDP port {CDP_PORT} is responding but Antigravity process not detected by psutil. "
                            "Triggers will not fire until the process is found. "
                            "Verify the process name via Task Manager.",
                            level="WARN",
                        )
                    no_cdp_warned_at = time.time()
                time.sleep(POLL_INTERVAL)
                continue

            # ── 3. Ensure network listener is alive ───────────────────────────
            # Re-establish the listener if it dropped (e.g. the workbench window
            # was reloaded or a new target appeared after startup).
            with _network_listener_lock:
                listener_alive = (
                    _network_listener is not None
                    and not _network_listener._closed
                    and _network_listener._sock is not None
                )
            if not listener_alive:
                log("NetworkListener: not connected — attempting reconnect...", level="DEBUG")
                setup_network_listener()

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
