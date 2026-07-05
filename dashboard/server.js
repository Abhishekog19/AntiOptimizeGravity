const express = require("express");
const path = require("path");
const store = require("./db");

const app = express();
app.use(express.json());
app.use(express.static(path.join(__dirname, "public")));

const API_KEY = process.env.DASHBOARD_API_KEY || "";

function requireApiKey(req, res, next) {
  if (!API_KEY) return next(); // no key configured -> open (local/dev use only)
  const auth = req.headers.authorization || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
  if (token !== API_KEY) return res.status(401).json({ error: "invalid or missing API key" });
  next();
}

// Extension -> dashboard sync endpoint.
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

// Overview grid + recommendation.
app.get("/api/accounts", (_req, res) => {
  res.json(store.listAccountsWithLatest());
});

// Detail view history graph.
app.get("/api/accounts/:id/history", (req, res) => {
  res.json(store.getHistory(req.params.id));
});

// Custom display name.
app.patch("/api/accounts/:id", requireApiKey, (req, res) => {
  const { displayName } = req.body || {};
  if (!displayName) return res.status(400).json({ error: "displayName is required" });
  store.setCustomName(req.params.id, displayName);
  res.json({ ok: true });
});

const PORT = process.env.PORT || 4300;
app.listen(PORT, () => {
  console.log(`Antigravity Quota Dashboard listening on http://localhost:${PORT}`);
  if (!API_KEY) {
    console.warn("DASHBOARD_API_KEY not set — sync endpoint is open. Fine for local use, not for the open internet.");
  }
});
