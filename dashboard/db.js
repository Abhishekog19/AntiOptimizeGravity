const { DatabaseSync } = require("node:sqlite");
const path = require("path");
const fs = require("fs");

const dataDir = path.join(__dirname, "data");
if (!fs.existsSync(dataDir)) fs.mkdirSync(dataDir, { recursive: true });

const db = new DatabaseSync(path.join(dataDir, "quota.db"));

db.exec(`
  CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    custom_name TEXT
  );

  CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    claude_weekly_pct REAL,
    claude_fivehour_pct REAL,
    claude_reset_raw TEXT,
    claude_fivehour_reset_raw TEXT,
    claude_weekly_reset_at TEXT,
    claude_fivehour_reset_at TEXT,
    gemini_weekly_pct REAL,
    gemini_fivehour_pct REAL,
    gemini_reset_raw TEXT,
    gemini_fivehour_reset_raw TEXT,
    gemini_weekly_reset_at TEXT,
    gemini_fivehour_reset_at TEXT,
    FOREIGN KEY (account_id) REFERENCES accounts(id)
  );

  CREATE INDEX IF NOT EXISTS idx_readings_account_time
    ON readings (account_id, timestamp_utc);
`);

// Add new raw-countdown columns to existing databases that were created before
// this schema change (ALTER TABLE IF NOT EXISTS is not valid SQL; we use a
// try/catch instead).
for (const col of [
  "ALTER TABLE readings ADD COLUMN claude_fivehour_reset_raw TEXT",
  "ALTER TABLE readings ADD COLUMN gemini_fivehour_reset_raw TEXT",
]) {
  try { db.exec(col); } catch { /* column already exists — ignore */ }
}

// --- Reset-time parsing -----------------------------------------------

/** Parses strings like "fully refresh in 4 days" / "resets in 3 hours 42 minutes" into an ISO timestamp.
 *  Handles compound durations: "6 days, 22 hours" → adds both components.
 */
function parseCountdownToIso(countdownRaw, fromIso) {
  if (!countdownRaw) return null;
  const from = new Date(fromIso);
  let totalMs = 0;
  const re = /(\d+(?:\.\d+)?)\s*(day|hour|minute)s?/gi;
  let m;
  while ((m = re.exec(countdownRaw)) !== null) {
    const amount = parseFloat(m[1]);
    const unit = m[2].toLowerCase();
    const msPerUnit = { day: 86400000, hour: 3600000, minute: 60000 }[unit];
    totalMs += msPerUnit * amount;
  }
  return totalMs > 0 ? new Date(from.getTime() + totalMs).toISOString() : null;
}

// --- Writes --------------------------------------------------------------

const insertAccountStmt = db.prepare(
  `INSERT INTO accounts (id, custom_name) VALUES (?, NULL)
   ON CONFLICT(id) DO NOTHING`
);

const insertReadingStmt = db.prepare(`
  INSERT INTO readings (
    account_id, timestamp_utc,
    claude_weekly_pct, claude_fivehour_pct,
    claude_reset_raw, claude_fivehour_reset_raw,
    claude_weekly_reset_at, claude_fivehour_reset_at,
    gemini_weekly_pct, gemini_fivehour_pct,
    gemini_reset_raw, gemini_fivehour_reset_raw,
    gemini_weekly_reset_at, gemini_fivehour_reset_at
  ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
`);

function upsertReading(reading) {
  const { accountId, timestampUtc, claudeGpt, gemini } = reading;

  insertAccountStmt.run(accountId);

  // Use the per-row countdown text extracted by ocr.js (Bug 1 fix).
  // Falls back to null when the text is missing (e.g. manual entry with no countdown).
  const claudeWeeklyResetAt   = parseCountdownToIso(claudeGpt?.weeklyResetRaw, timestampUtc);
  const claudeFiveHourResetAt = parseCountdownToIso(claudeGpt?.fiveHourResetRaw, timestampUtc);
  const geminiWeeklyResetAt   = gemini ? parseCountdownToIso(gemini.weeklyResetRaw, timestampUtc) : null;
  const geminiFiveHourResetAt = gemini ? parseCountdownToIso(gemini.fiveHourResetRaw, timestampUtc) : null;

  insertReadingStmt.run(
    accountId,
    timestampUtc,
    claudeGpt?.weeklyPct ?? null,
    claudeGpt?.fiveHourPct ?? null,
    claudeGpt?.weeklyResetRaw ?? null,
    claudeGpt?.fiveHourResetRaw ?? null,
    claudeWeeklyResetAt,
    claudeFiveHourResetAt,
    gemini?.weeklyPct ?? null,
    gemini?.fiveHourPct ?? null,
    gemini?.weeklyResetRaw ?? null,
    gemini?.fiveHourResetRaw ?? null,
    geminiWeeklyResetAt,
    geminiFiveHourResetAt
  );
}

function setCustomName(accountId, customName) {
  db.prepare(
    `INSERT INTO accounts (id, custom_name) VALUES (?, ?)
     ON CONFLICT(id) DO UPDATE SET custom_name = excluded.custom_name`
  ).run(accountId, customName);
}

// --- Reads ---------------------------------------------------------------

/**
 * Ranks by effective five-hour capacity.
 *
 * NOTE: stored pct values are **% REMAINING** (as shown in the Antigravity UI),
 * not % consumed.  Do NOT subtract from 100.
 */
function recommendationScore(latest) {
  if (latest.claude_weekly_pct == null || latest.claude_fivehour_pct == null) return -Infinity;
  // Values are already "remaining" — use directly.
  const weeklyRemaining   = latest.claude_weekly_pct;
  const fiveHourRemaining = latest.claude_fivehour_pct;
  const FULL_SESSION_WEEKLY_COST = 33; // ~1/3 of weekly per full five-hour session
  const weeklyCappedCapacity = (weeklyRemaining / FULL_SESSION_WEEKLY_COST) * 100;
  return Math.min(fiveHourRemaining, weeklyCappedCapacity);
}

function listAccountsWithLatest() {
  const accounts = db.prepare(`SELECT id, custom_name FROM accounts`).all();
  const latestStmt = db.prepare(
    `SELECT * FROM readings WHERE account_id = ? ORDER BY timestamp_utc DESC LIMIT 1`
  );
  const rows = accounts.map((a) => {
    const latest = latestStmt.get(a.id) || null;
    return {
      id: a.id,
      displayName: a.custom_name || a.id,
      latest,
      score: latest ? recommendationScore(latest) : -Infinity,
    };
  });
  rows.sort((a, b) => b.score - a.score);
  return rows;
}

function getHistory(accountId, limit = 500) {
  return db
    .prepare(`SELECT * FROM readings WHERE account_id = ? ORDER BY timestamp_utc ASC LIMIT ?`)
    .all(accountId, limit);
}

// --- Analytics ----------------------------------------------------------

/**
 * Returns pre-computed analytics data for the dashboard Analytics tab.
 * @param {number|null} days  - Number of days to look back, or null for all time.
 */
function getAnalytics(days) {
  const cutoff = days
    ? new Date(Date.now() - days * 86400000).toISOString()
    : "1970-01-01T00:00:00.000Z";

  // ── Burn-rate series (one point per day per account) ──────────────────────
  const accounts = db.prepare(`SELECT id, custom_name FROM accounts`).all();

  const seriesStmt = db.prepare(`
    SELECT
      date(timestamp_utc) AS day,
      AVG(claude_weekly_pct) AS avg_claude_weekly,
      AVG(claude_fivehour_pct) AS avg_claude_fivehour,
      AVG(gemini_weekly_pct) AS avg_gemini_weekly
    FROM readings
    WHERE account_id = ? AND timestamp_utc >= ?
    GROUP BY date(timestamp_utc)
    ORDER BY day ASC
  `);

  const series = accounts.map((a) => ({
    accountId: a.id,
    displayName: a.custom_name || a.id,
    points: seriesStmt.all(a.id, cutoff).map((r) => ({
      day: r.day,
      claudeWeeklyPct: r.avg_claude_weekly,
      claudeFiveHourPct: r.avg_claude_fivehour,
      geminiWeeklyPct: r.avg_gemini_weekly,
    })),
  }));

  // ── Heatmap: avg Claude weekly CONSUMED per day-of-week ────────────────────────
  // Stored pct = remaining, so consumption = 100 - remaining.
  // SQLite strftime('%w') → 0=Sun … 6=Sat; we remap to 0=Mon … 6=Sun.
  const heatmapRows = db.prepare(`
    SELECT
      (CAST(strftime('%w', timestamp_utc) AS INTEGER) + 6) % 7 AS dow,
      (100 - AVG(claude_weekly_pct)) AS avg_consumed,
      COUNT(*) AS n
    FROM readings
    WHERE timestamp_utc >= ?
    GROUP BY dow
    ORDER BY dow ASC
  `).all(cutoff);

  const DOW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const heatmap = DOW_LABELS.map((label, i) => {
    const row = heatmapRows.find((r) => r.dow === i);
    return { dow: i, label, avgPct: row ? row.avg_consumed : null, count: row ? row.n : 0 };
  });

  // ── Session count (5-hour-limit resets observed) ──────────────────────────
  // Proxy: count readings where fivehour_pct is low (< 15%) — implies a fresh reset.
  // This is a heuristic; a more precise version would require consecutive-reading diffs.
  const sessionCount = db.prepare(`
    SELECT COUNT(*) AS cnt
    FROM readings
    WHERE claude_fivehour_pct < 15 AND timestamp_utc >= ?
  `).get(cutoff)?.cnt ?? 0;

  // ── Projection: days until all accounts exhaust weekly quota ─────────────
  // Burn rate = avg (claude_weekly_pct increase) per day over last 7 days.
  const last7Cutoff = new Date(Date.now() - 7 * 86400000).toISOString();
  let daysRemaining = null;

  try {
    // For each account with ≥2 readings in last 7 days, compute pct/day increase.
    const burnRows = db.prepare(`
      SELECT account_id,
        (MAX(claude_weekly_pct) - MIN(claude_weekly_pct)) AS delta_pct,
        (julianday(MAX(timestamp_utc)) - julianday(MIN(timestamp_utc))) AS delta_days
      FROM readings
      WHERE timestamp_utc >= ? AND claude_weekly_pct IS NOT NULL
      GROUP BY account_id
      HAVING COUNT(*) >= 2 AND delta_days > 0
    `).all(last7Cutoff);

    if (burnRows.length > 0) {
      // For each account, estimate days until it hits 100%; take the min across accounts
      // (the "last account standing"), then average the burn rate across all for the projection.
      const ratesPerDay = burnRows.map((r) => r.delta_pct / r.delta_days);
      const avgRate = ratesPerDay.reduce((a, b) => a + b, 0) / ratesPerDay.length;

      // Latest weekly pct across all accounts (worst = highest usage)
      const latestRows = db.prepare(`
        SELECT account_id, claude_weekly_pct
        FROM readings r
        WHERE timestamp_utc = (
          SELECT MAX(timestamp_utc) FROM readings WHERE account_id = r.account_id
        )
      `).all();

      const maxUsed = Math.max(...latestRows.map((r) => r.claude_weekly_pct ?? 0));
      // pct is "remaining" — the most constrained account is the one with the LEAST remaining.
      const minRemaining = Math.min(...latestRows.map((r) => r.claude_weekly_pct ?? 100));
      daysRemaining = avgRate > 0 ? Math.max(0, minRemaining / avgRate) : null;
    }
  } catch { /* non-fatal */ }

  return { series, heatmap, sessionCount, daysRemaining };
}

module.exports = { upsertReading, setCustomName, listAccountsWithLatest, getHistory, getAnalytics };
