"""
server/flask_app.py — Flask dashboard server for Antigravity Quota Tracker

Routes
──────
  GET  /                         → serve index.html
  GET  /api/accounts             → list accounts + latest reading
  GET  /api/accounts/<id>/history
  PATCH /api/accounts/<id>       → rename account
  GET  /api/analytics?range=week|month|year|max
  GET  /api/status               → notifier heartbeat status
  POST /api/heartbeat            → notifier posts here every 15 s
  POST /api/readings             → store a new quota reading

Static files are served from dashboard/public/.
"""

from __future__ import annotations
import os
import time
import logging
from pathlib import Path
from flask import (
    Flask, request, jsonify, send_from_directory, send_file
)
from server import db

log = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────────
_HERE       = Path(__file__).parent.parent          # project root
_PUBLIC_DIR = _HERE / "dashboard" / "public"        # static files (unchanged)

# ── Config (from environment / .env loaded by main.py) ────────────────────────
API_KEY        = os.environ.get("DASHBOARD_API_KEY", "")
PORT           = int(os.environ.get("PORT", "4300"))

# ── In-memory heartbeat state ──────────────────────────────────────────────────
# The notifier POSTs here every 15 s.  The dashboard polls GET /api/status.
_heartbeat: dict = {
    "receivedAt":    None,
    "status":        None,
    "lastCaptureAt": None,
    "triggerCount":  0,
    "lastTrigger":   None,
    "version":       None,
}

# ── Flask app factory ──────────────────────────────────────────────────────────

def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=str(_PUBLIC_DIR),
        static_url_path="",
    )
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB upload limit

    # ── Auth middleware ────────────────────────────────────────────────────────
    def _require_api_key():
        if not API_KEY:
            return None  # open — local/dev mode
        auth  = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if token != API_KEY:
            return jsonify({"error": "invalid or missing API key"}), 401
        return None

    # ── Static / index ─────────────────────────────────────────────────────────

    @app.route("/")
    def index():
        return send_from_directory(str(_PUBLIC_DIR), "index.html")

    # ── POST /api/heartbeat ────────────────────────────────────────────────────

    @app.route("/api/heartbeat", methods=["POST"])
    def post_heartbeat():
        global _heartbeat
        body = request.get_json(silent=True) or {}
        _heartbeat = {
            "receivedAt":    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status":        body.get("status",        "live"),
            "lastCaptureAt": body.get("lastCaptureAt") or _heartbeat["lastCaptureAt"],
            "triggerCount":  body.get("triggerCount",  _heartbeat["triggerCount"]),
            "lastTrigger":   body.get("lastTrigger")   or _heartbeat["lastTrigger"],
            "version":       body.get("version")       or _heartbeat["version"],
        }
        return jsonify({"ok": True})

    # ── GET /api/status ────────────────────────────────────────────────────────

    @app.route("/api/status")
    def get_status():
        if _heartbeat["receivedAt"]:
            import datetime
            recv = datetime.datetime.strptime(_heartbeat["receivedAt"], "%Y-%m-%dT%H:%M:%SZ")
            recv = recv.replace(tzinfo=datetime.timezone.utc)
            age_ms = (datetime.datetime.now(datetime.timezone.utc) - recv).total_seconds() * 1000
        else:
            age_ms = float("inf")

        connectivity = (
            "live"    if age_ms < 30_000  else
            "stale"   if age_ms < 120_000 else
            "offline"
        )
        return jsonify({
            "connectivity":        connectivity,
            "heartbeatAgeSeconds": round(age_ms / 1000) if age_ms != float("inf") else None,
            **_heartbeat,
        })

    # ── POST /api/readings ─────────────────────────────────────────────────────

    @app.route("/api/readings", methods=["POST"])
    def post_readings():
        err = _require_api_key()
        if err:
            return err
        body = request.get_json(silent=True) or {}

        # Normalise notifier format → UI format
        if "capturedAt" in body and "quota" in body:
            def fix_section(s):
                if not s:
                    return None
                out = dict(s)
                out.setdefault("weeklyResetRaw",   out.pop("weeklyReset",   None))
                out.setdefault("fiveHourResetRaw", out.pop("fiveHourReset", None))
                return out
            body = {
                "accountId":    body["accountId"],
                "timestampUtc": body["capturedAt"],
                "claudeGpt":    fix_section(body["quota"].get("claudeGpt")),
                "gemini":       fix_section(body["quota"].get("gemini")),
            }

        if not body.get("accountId") or not body.get("timestampUtc") or not body.get("claudeGpt"):
            return jsonify({
                "error": "accountId, timestampUtc (or capturedAt), and claudeGpt are required"
            }), 400

        try:
            db.upsert_reading(body)
            # Refresh accounts cache in shared state
            try:
                from state import app_state
                app_state.set_accounts(db.list_accounts_with_latest())
            except Exception:
                pass
            return jsonify({"ok": True}), 201
        except Exception as exc:
            log.error(f"[readings] {exc}")
            return jsonify({"error": "failed to store reading"}), 500

    # ── GET /api/accounts ──────────────────────────────────────────────────────

    @app.route("/api/accounts")
    def get_accounts():
        return jsonify(db.list_accounts_with_latest())

    # ── GET /api/accounts/<id>/history ────────────────────────────────────────

    @app.route("/api/accounts/<path:account_id>/history")
    def get_account_history(account_id):
        return jsonify(db.get_history(account_id))

    # ── PATCH /api/accounts/<id> ───────────────────────────────────────────────

    @app.route("/api/accounts/<path:account_id>", methods=["PATCH"])
    def patch_account(account_id):
        err = _require_api_key()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        display_name = body.get("displayName")
        if not display_name:
            return jsonify({"error": "displayName is required"}), 400
        db.set_custom_name(account_id, display_name)
        return jsonify({"ok": True})

    # ── GET /api/analytics ────────────────────────────────────────────────────

    @app.route("/api/analytics")
    def get_analytics():
        RANGES = {"week": 7, "month": 30, "year": 365, "max": None}
        range_name = request.args.get("range", "week")
        days = RANGES.get(range_name, 7)
        try:
            return jsonify(db.get_analytics(days))
        except Exception as exc:
            log.error(f"[analytics] {exc}")
            return jsonify({"error": "analytics query failed", "detail": str(exc)}), 500

    return app



def run_flask(host: str = "0.0.0.0", port: int = PORT, debug: bool = False) -> None:
    """Start Flask (blocking). Call in a daemon thread from main.py."""
    from state import app_state
    app = create_app()

    # Suppress werkzeug startup banner — we print our own
    import logging as _logging
    _logging.getLogger("werkzeug").setLevel(_logging.WARNING)

    app_state.log("Flask dashboard starting on port 4300...", app_state.LEVEL_INFO)
    app_state.set_flask_ready(True)
    app.run(host=host, port=port, debug=debug, use_reloader=False)
