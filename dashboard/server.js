require("dotenv").config();

const express = require("express");
const path = require("path");
const multer = require("multer");
const fs = require("fs");
const store = require("./db");
const { ocrImage } = require("./ocr");

const app = express();
app.use(express.json());
app.use(express.static(path.join(__dirname, "public")));

// ── multer: memory storage for OCR uploads (no disk write in server layer) ──
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 20 * 1024 * 1024 }, // 20 MB ceiling
  fileFilter(_req, file, cb) {
    if (file.mimetype.startsWith("image/")) return cb(null, true);
    cb(new Error("Only image files are accepted"));
  },
});

// ── Tesseract config (from env, not hardcoded) ────────────────────────────────
const TESSERACT_PATH = process.env.TESSERACT_PATH || "tesseract";
const OCR_PSM = process.env.OCR_PSM || "3";
const OCR_UPSCALE = parseInt(process.env.OCR_UPSCALE || "2", 10);

function tesseractAvailable() {
  try {
    // Quick probe — just run tesseract with no args; it exits non-zero but
    // we only care that the binary is found (no ENOENT).
    const { execSync } = require("child_process");
    execSync(`"${TESSERACT_PATH}" --version`, { stdio: "pipe", timeout: 5000 });
    return true;
  } catch (e) {
    // ENOENT means not found; any other exit code still means it's present.
    return e.code !== "ENOENT" && !String(e.message).includes("ENOENT");
  }
}

// ── API key middleware ────────────────────────────────────────────────────────
const API_KEY = process.env.DASHBOARD_API_KEY || "";

function requireApiKey(req, res, next) {
  if (!API_KEY) return next(); // no key configured → open (local/dev only)
  const auth = req.headers.authorization || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
  if (token !== API_KEY) return res.status(401).json({ error: "invalid or missing API key" });
  next();
}

// ── POST /api/readings — manual save (existing route, unchanged) ──────────────
app.post("/api/readings", requireApiKey, (req, res) => {
  const { accountId, timestampUtc, claudeGpt } = req.body || {};
  if (!accountId || !timestampUtc || !claudeGpt) {
    return res.status(400).json({ error: "accountId, timestampUtc, and claudeGpt are required" });
  }
  try {
    store.upsertReading(req.body);
    res.status(201).json({ ok: true });
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: "failed to store reading" });
  }
});

// ── POST /api/ocr — image → parsed quota numbers ──────────────────────────────
//    Accepts multipart/form-data with field "image".
//    Returns: { claudeGpt, gemini, confidence, rawText } or { error }.
app.post("/api/ocr", upload.single("image"), (req, res) => {
  if (!req.file) {
    return res.status(400).json({ error: "No image uploaded. Send a multipart/form-data request with field 'image'." });
  }

  if (!tesseractAvailable()) {
    return res.status(503).json({
      error: "Tesseract not found",
      detail: `Could not locate Tesseract at "${TESSERACT_PATH}". ` +
        "Set TESSERACT_PATH in your .env file (see .env.example).",
    });
  }

  try {
    const { text, quota } = ocrImage(
      req.file.buffer,
      req.file.mimetype,
      TESSERACT_PATH,
      OCR_PSM,
      OCR_UPSCALE
    );

    if (!quota) {
      return res.status(422).json({
        error: "Could not parse quota numbers from this image.",
        rawText: text,
        confidence: "low",
      });
    }

    const confidence =
      quota.claudeGpt?.weeklyPct != null && quota.claudeGpt?.fiveHourPct != null
        ? "high"
        : "low";

    res.json({ ...quota, confidence, rawText: text });
  } catch (err) {
    console.error("[OCR]", err.message);
    res.status(500).json({ error: "OCR processing failed", detail: err.message });
  }
});

// ── GET /api/accounts — overview grid + recommendation (unchanged) ────────────
app.get("/api/accounts", (_req, res) => {
  res.json(store.listAccountsWithLatest());
});

// ── GET /api/accounts/:id/history — detail view (unchanged) ──────────────────
app.get("/api/accounts/:id/history", (req, res) => {
  res.json(store.getHistory(req.params.id));
});

// ── PATCH /api/accounts/:id — rename (unchanged) ─────────────────────────────
app.patch("/api/accounts/:id", requireApiKey, (req, res) => {
  const { displayName } = req.body || {};
  if (!displayName) return res.status(400).json({ error: "displayName is required" });
  store.setCustomName(req.params.id, displayName);
  res.json({ ok: true });
});

// ── GET /api/analytics?range=week|month|year|max ──────────────────────────────
app.get("/api/analytics", (req, res) => {
  const RANGES = { week: 7, month: 30, year: 365, max: null };
  const range = req.query.range || "week";
  const days = RANGES.hasOwnProperty(range) ? RANGES[range] : 7;
  try {
    res.json(store.getAnalytics(days));
  } catch (err) {
    console.error("[Analytics]", err.message);
    res.status(500).json({ error: "analytics query failed", detail: err.message });
  }
});

// ── GET /api/settings — surfaces config/health state to the UI ────────────────
app.get("/api/settings", (_req, res) => {
  const tAvail = tesseractAvailable();
  res.json({
    tesseract: {
      path: TESSERACT_PATH,
      available: tAvail,
      warning: tAvail
        ? null
        : `Tesseract not found at "${TESSERACT_PATH}". OCR captures will not work. ` +
          "Copy .env.example to .env and set TESSERACT_PATH.",
    },
    ocrPsm: OCR_PSM,
    ocrUpscale: OCR_UPSCALE,
    apiKeyConfigured: !!API_KEY,
  });
});

// ── Start ─────────────────────────────────────────────────────────────────────
const PORT = process.env.PORT || 4300;
app.listen(PORT, () => {
  console.log(`Antigravity Quota Dashboard → http://localhost:${PORT}`);
  if (!API_KEY) {
    console.warn("DASHBOARD_API_KEY not set — sync endpoint is open. Fine for local use.");
  }
  if (!tesseractAvailable()) {
    console.warn(
      `⚠  Tesseract not found at "${TESSERACT_PATH}". ` +
      "OCR endpoint will return 503 until configured. See .env.example."
    );
  }
});
