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
    claude_weekly_reset_at TEXT,
    claude_fivehour_reset_at TEXT,
    gemini_weekly_pct REAL,
    gemini_fivehour_pct REAL,
    gemini_reset_raw TEXT,
    gemini_weekly_reset_at TEXT,
    gemini_fivehour_reset_at TEXT,
    FOREIGN KEY (account_id) REFERENCES accounts(id)
  );

  CREATE INDEX IF NOT EXISTS idx_readings_account_time
    ON readings (account_id, timestamp_utc);
`);

// --- Reset-time parsing -----------------------------------------------

/** Parses strings like "fully refresh in 4 days" / "resets in 3 hours" into an ISO timestamp. */
function parseCountdownToIso(countdownRaw, fromIso) {
  if (!countdownRaw) return null;
  const from = new Date(fromIso);
  const match = countdownRaw.match(/(\d+(?:\.\d+)?)\s*(day|hour|minute)s?/i);
  if (!match) return null;
  const amount = parseFloat(match[1]);
  const unit = match[2].toLowerCase();
  const msPerUnit = { day: 86400000, hour: 3600000, minute: 60000 }[unit];
  return new Date(from.getTime() + msPerUnit * amount).toISOString();
}

/** Five Hour Limit is a fixed rolling window, always ~5h from the capture time. */
function fiveHourResetFromCapture(fromIso) {
  return new Date(new Date(fromIso).getTime() + 5 * 3600000).toISOString();
}

// --- Writes --------------------------------------------------------------

const insertAccountStmt = db.prepare(
  `INSERT INTO accounts (id, custom_name) VALUES (?, NULL)
   ON CONFLICT(id) DO NOTHING`
);

const insertReadingStmt = db.prepare(`
  INSERT INTO readings (
    account_id, timestamp_utc,
    claude_weekly_pct, claude_fivehour_pct, claude_reset_raw, claude_weekly_reset_at, claude_fivehour_reset_at,
    gemini_weekly_pct, gemini_fivehour_pct, gemini_reset_raw, gemini_weekly_reset_at, gemini_fivehour_reset_at
  ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
`);

function upsertReading(reading) {
  const { accountId, timestampUtc, claudeGpt, gemini } = reading;

  insertAccountStmt.run(accountId);

  const claudeWeeklyResetAt = parseCountdownToIso(claudeGpt?.resetCountdownRaw, timestampUtc);
  const claudeFiveHourResetAt = fiveHourResetFromCapture(timestampUtc);
  const geminiWeeklyResetAt = gemini ? parseCountdownToIso(gemini.resetCountdownRaw, timestampUtc) : null;
  const geminiFiveHourResetAt = gemini ? fiveHourResetFromCapture(timestampUtc) : null;

  insertReadingStmt.run(
    accountId,
    timestampUtc,
    claudeGpt?.weeklyPct ?? null,
    claudeGpt?.fiveHourPct ?? null,
    claudeGpt?.resetCountdownRaw ?? null,
    claudeWeeklyResetAt,
    claudeFiveHourResetAt,
    gemini?.weeklyPct ?? null,
    gemini?.fiveHourPct ?? null,
    gemini?.resetCountdownRaw ?? null,
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

/** Ranks by effective five-hour capacity, discounted when weekly balance can't cover a full session. */
function recommendationScore(latest) {
  if (latest.claude_weekly_pct == null || latest.claude_fivehour_pct == null) return -Infinity;
  const weeklyRemaining = 100 - latest.claude_weekly_pct;
  const fiveHourRemaining = 100 - latest.claude_fivehour_pct;
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

module.exports = { upsertReading, setCustomName, listAccountsWithLatest, getHistory };
