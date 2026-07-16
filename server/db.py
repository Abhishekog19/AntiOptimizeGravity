"""
server/db.py — SQLite database layer for Antigravity Quota Tracker

Python port of dashboard/db.js.  Uses the stdlib sqlite3 module.
Same schema, same column names, same query semantics.

Database path: dashboard/data/quota.db  (same as the Node.js version
so that any existing data is reused without migration)
"""

from __future__ import annotations
import sqlite3
import os
import re
import datetime
import math
from pathlib import Path
from typing import Optional

# ── Database location ─────────────────────────────────────────────────────────
# Stored at the same path as the old Node.js version for backwards-compat.
_HERE    = Path(__file__).parent.parent  # project root
_DATA_DIR = _HERE / "dashboard" / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_DB_PATH  = _DATA_DIR / "quota.db"

# ── Connection (module-level singleton, check_same_thread=False for Flask) ────
_conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
_conn.row_factory = sqlite3.Row           # rows behave like dicts

# ── WAL mode for concurrent reads from Flask + background threads ─────────────
_conn.execute("PRAGMA journal_mode=WAL")
_conn.execute("PRAGMA foreign_keys=ON")

# ── Schema ────────────────────────────────────────────────────────────────────
_conn.executescript("""
    CREATE TABLE IF NOT EXISTS accounts (
        id          TEXT PRIMARY KEY,
        custom_name TEXT
    );

    CREATE TABLE IF NOT EXISTS readings (
        id                        INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id                TEXT    NOT NULL,
        timestamp_utc             TEXT    NOT NULL,
        claude_weekly_pct         REAL,
        claude_fivehour_pct       REAL,
        claude_reset_raw          TEXT,
        claude_fivehour_reset_raw TEXT,
        claude_weekly_reset_at    TEXT,
        claude_fivehour_reset_at  TEXT,
        gemini_weekly_pct         REAL,
        gemini_fivehour_pct       REAL,
        gemini_reset_raw          TEXT,
        gemini_fivehour_reset_raw TEXT,
        gemini_weekly_reset_at    TEXT,
        gemini_fivehour_reset_at  TEXT,
        FOREIGN KEY (account_id) REFERENCES accounts(id)
    );

    CREATE INDEX IF NOT EXISTS idx_readings_account_time
        ON readings (account_id, timestamp_utc);
""")

# ── Migration: add columns added after initial schema ─────────────────────────
for _col_sql in [
    "ALTER TABLE readings ADD COLUMN claude_fivehour_reset_raw TEXT",
    "ALTER TABLE readings ADD COLUMN gemini_fivehour_reset_raw TEXT",
]:
    try:
        _conn.execute(_col_sql)
        _conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

# ── Lock for writes (sqlite3 in WAL handles concurrent reads fine, but
#    concurrent writes still need serialisation from our side) ─────────────────
import threading
_write_lock = threading.Lock()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_countdown_to_iso(countdown_raw: Optional[str], from_iso: str) -> Optional[str]:
    """
    Convert a free-text countdown like '6 days, 22 hours' into an ISO timestamp.
    Mirrors parseCountdownToIso() from db.js.
    """
    if not countdown_raw:
        return None
    from_dt  = datetime.datetime.fromisoformat(from_iso.replace("Z", "+00:00"))
    total_ms = 0
    for amount_s, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(day|hour|minute)s?", countdown_raw, re.I):
        amount  = float(amount_s)
        ms_per  = {"day": 86_400_000, "hour": 3_600_000, "minute": 60_000}[unit.lower()]
        total_ms += ms_per * amount
    if total_ms <= 0:
        return None
    result = from_dt + datetime.timedelta(milliseconds=total_ms)
    return result.isoformat()


# ── Writes ────────────────────────────────────────────────────────────────────

def upsert_reading(reading: dict) -> None:
    """
    Insert a new quota reading.  reading dict accepts two shapes:

    UI shape:       { accountId, timestampUtc, claudeGpt, gemini? }
    Notifier shape: { accountId, capturedAt, trigger, quota:{claudeGpt,gemini?} }
    """
    # Normalise notifier format
    if "capturedAt" in reading and "quota" in reading:
        def fix_section(s):
            if not s:
                return None
            out = dict(s)
            out.setdefault("weeklyResetRaw",   out.pop("weeklyReset",   None))
            out.setdefault("fiveHourResetRaw", out.pop("fiveHourReset", None))
            return out
        reading = {
            "accountId":    reading["accountId"],
            "timestampUtc": reading["capturedAt"],
            "claudeGpt":    fix_section(reading["quota"].get("claudeGpt")),
            "gemini":       fix_section(reading["quota"].get("gemini")),
        }

    account_id    = reading["accountId"]
    timestamp_utc = reading["timestampUtc"]
    claude_gpt    = reading.get("claudeGpt") or {}
    gemini        = reading.get("gemini")    or {}

    claude_weekly_reset_at    = _parse_countdown_to_iso(claude_gpt.get("weeklyResetRaw"),   timestamp_utc)
    claude_fivehour_reset_at  = _parse_countdown_to_iso(claude_gpt.get("fiveHourResetRaw"), timestamp_utc)
    gemini_weekly_reset_at    = _parse_countdown_to_iso(gemini.get("weeklyResetRaw"),   timestamp_utc) if gemini else None
    gemini_fivehour_reset_at  = _parse_countdown_to_iso(gemini.get("fiveHourResetRaw"), timestamp_utc) if gemini else None

    with _write_lock:
        _conn.execute(
            "INSERT INTO accounts (id, custom_name) VALUES (?, NULL) ON CONFLICT(id) DO NOTHING",
            (account_id,)
        )
        _conn.execute("""
            INSERT INTO readings (
                account_id, timestamp_utc,
                claude_weekly_pct, claude_fivehour_pct,
                claude_reset_raw, claude_fivehour_reset_raw,
                claude_weekly_reset_at, claude_fivehour_reset_at,
                gemini_weekly_pct, gemini_fivehour_pct,
                gemini_reset_raw, gemini_fivehour_reset_raw,
                gemini_weekly_reset_at, gemini_fivehour_reset_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            account_id, timestamp_utc,
            claude_gpt.get("weeklyPct"),
            claude_gpt.get("fiveHourPct"),
            claude_gpt.get("weeklyResetRaw"),
            claude_gpt.get("fiveHourResetRaw"),
            claude_weekly_reset_at,
            claude_fivehour_reset_at,
            gemini.get("weeklyPct")    if gemini else None,
            gemini.get("fiveHourPct") if gemini else None,
            gemini.get("weeklyResetRaw")    if gemini else None,
            gemini.get("fiveHourResetRaw")  if gemini else None,
            gemini_weekly_reset_at,
            gemini_fivehour_reset_at,
        ))
        _conn.commit()


def set_custom_name(account_id: str, custom_name: str) -> None:
    with _write_lock:
        _conn.execute("""
            INSERT INTO accounts (id, custom_name) VALUES (?, ?)
            ON CONFLICT(id) DO UPDATE SET custom_name = excluded.custom_name
        """, (account_id, custom_name))
        _conn.commit()


# ── Reads ─────────────────────────────────────────────────────────────────────

def recommendation_score(latest) -> float:
    """
    Rank by effective five-hour capacity.
    Mirrors recommendationScore() from db.js.
    Values are % REMAINING (not % consumed).
    """
    if latest is None:
        return -math.inf
    w = latest["claude_weekly_pct"]
    f = latest["claude_fivehour_pct"]
    if w is None or f is None:
        return -math.inf
    FULL_SESSION_WEEKLY_COST = 33
    weekly_capped = (w / FULL_SESSION_WEEKLY_COST) * 100
    return min(f, weekly_capped)


def list_accounts_with_latest() -> list:
    """Return all accounts with their latest reading, sorted by recommendation score."""
    accounts = _conn.execute("SELECT id, custom_name FROM accounts").fetchall()
    rows = []
    for a in accounts:
        latest = _conn.execute(
            "SELECT * FROM readings WHERE account_id = ? ORDER BY timestamp_utc DESC LIMIT 1",
            (a["id"],)
        ).fetchone()
        latest_dict = dict(latest) if latest else None
        rows.append({
            "id":          a["id"],
            "displayName": a["custom_name"] or a["id"],
            "latest":      latest_dict,
            "score":       recommendation_score(latest_dict),
        })
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


def get_history(account_id: str, limit: int = 500) -> list:
    rows = _conn.execute(
        "SELECT * FROM readings WHERE account_id = ? ORDER BY timestamp_utc ASC LIMIT ?",
        (account_id, limit)
    ).fetchall()
    return [dict(r) for r in rows]


# ── Analytics ─────────────────────────────────────────────────────────────────

def get_analytics(days: Optional[int]) -> dict:
    """
    Pre-computed analytics for the dashboard Analytics tab.
    Mirrors getAnalytics() from db.js.
    """
    if days:
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).isoformat()
    else:
        cutoff = "1970-01-01T00:00:00.000000"

    accounts = _conn.execute("SELECT id, custom_name FROM accounts").fetchall()

    # ── Burn-rate series ──────────────────────────────────────────────────────
    series = []
    for a in accounts:
        pts = _conn.execute("""
            SELECT
                date(timestamp_utc) AS day,
                AVG(claude_weekly_pct)    AS avg_claude_weekly,
                AVG(claude_fivehour_pct)  AS avg_claude_fivehour,
                AVG(gemini_weekly_pct)    AS avg_gemini_weekly
            FROM readings
            WHERE account_id = ? AND timestamp_utc >= ?
            GROUP BY date(timestamp_utc)
            ORDER BY day ASC
        """, (a["id"], cutoff)).fetchall()
        series.append({
            "accountId":   a["id"],
            "displayName": a["custom_name"] or a["id"],
            "points": [
                {
                    "day":              r["day"],
                    "claudeWeeklyPct":  r["avg_claude_weekly"],
                    "claudeFiveHourPct": r["avg_claude_fivehour"],
                    "geminiWeeklyPct":  r["avg_gemini_weekly"],
                }
                for r in pts
            ],
        })

    # ── Heatmap: avg Claude weekly consumed per day-of-week ───────────────────
    heatmap_rows = _conn.execute("""
        SELECT
            (CAST(strftime('%w', timestamp_utc) AS INTEGER) + 6) % 7 AS dow,
            (100 - AVG(claude_weekly_pct)) AS avg_consumed,
            COUNT(*) AS n
        FROM readings
        WHERE timestamp_utc >= ?
        GROUP BY dow
        ORDER BY dow ASC
    """, (cutoff,)).fetchall()

    DOW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    heatmap_map = {r["dow"]: r for r in heatmap_rows}
    heatmap = [
        {
            "dow":    i,
            "label":  DOW_LABELS[i],
            "avgPct": heatmap_map[i]["avg_consumed"] if i in heatmap_map else None,
            "count":  heatmap_map[i]["n"]            if i in heatmap_map else 0,
        }
        for i in range(7)
    ]

    # ── Session count ─────────────────────────────────────────────────────────
    session_count = _conn.execute(
        "SELECT COUNT(*) AS cnt FROM readings WHERE claude_fivehour_pct < 15 AND timestamp_utc >= ?",
        (cutoff,)
    ).fetchone()["cnt"] or 0

    # ── Projection ────────────────────────────────────────────────────────────
    last7_cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).isoformat()
    days_remaining = None
    try:
        burn_rows = _conn.execute("""
            SELECT account_id,
                (MAX(claude_weekly_pct) - MIN(claude_weekly_pct)) AS delta_pct,
                (julianday(MAX(timestamp_utc)) - julianday(MIN(timestamp_utc))) AS delta_days
            FROM readings
            WHERE timestamp_utc >= ? AND claude_weekly_pct IS NOT NULL
            GROUP BY account_id
            HAVING COUNT(*) >= 2 AND delta_days > 0
        """, (last7_cutoff,)).fetchall()

        if burn_rows:
            rates = [r["delta_pct"] / r["delta_days"] for r in burn_rows]
            avg_rate = sum(rates) / len(rates)
            latest_rows = _conn.execute("""
                SELECT account_id, claude_weekly_pct
                FROM readings r
                WHERE timestamp_utc = (
                    SELECT MAX(timestamp_utc) FROM readings WHERE account_id = r.account_id
                )
            """).fetchall()
            if latest_rows and avg_rate > 0:
                min_remaining = min(
                    (r["claude_weekly_pct"] for r in latest_rows if r["claude_weekly_pct"] is not None),
                    default=100
                )
                days_remaining = max(0, min_remaining / avg_rate)
    except Exception:
        pass

    return {
        "series":        series,
        "heatmap":       heatmap,
        "sessionCount":  session_count,
        "daysRemaining": days_remaining,
    }
