// ────────────────────────────────────────────────────────────────────────────
//  Antigravity Quota Dashboard — app.js
// ────────────────────────────────────────────────────────────────────────────

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtPct(v) {
  return v == null ? "—" : `${Math.round(v)}%`;
}

function timeUntil(iso) {
  if (!iso) return "—";
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "now";
  const totalMins = Math.floor(ms / 60000);
  const mins  = totalMins % 60;
  const hrs   = Math.floor(totalMins / 60) % 24;
  const days  = Math.floor(totalMins / 1440);
  if (days > 0 && hrs > 0) return `${days}d ${hrs}h`;
  if (days > 0)            return `${days}d`;
  if (hrs > 0 && mins > 0) return `${hrs}h ${mins}m`;
  if (hrs > 0)             return `${hrs}h`;
  return `${mins}m`;
}

function barClass(pct, base) {
  if (pct == null) return base;
  // pct = % REMAINING.  Danger/warn thresholds are now on LOW remaining.
  if (pct <= 10) return "danger";
  if (pct <= 30) return "warn";
  return base;
}

function isStale(iso) {
  if (!iso) return true;
  return Date.now() - new Date(iso).getTime() > 30 * 60 * 1000;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

// ── Clock ─────────────────────────────────────────────────────────────────────

function tickClock() {
  $("#clock").textContent = new Date().toUTCString().slice(17, 25) + " UTC";
}
tickClock();
setInterval(tickClock, 1000);

// ── Tab switching ─────────────────────────────────────────────────────────────

const TABS = ["overview", "capture", "analytics"];

function showTab(name) {
  TABS.forEach((t) => {
    $(`#tab-${t}`).classList.toggle("active", t === name);
    $(`#tab-${t}`).setAttribute("aria-selected", t === name ? "true" : "false");
    $(`#pane-${t}`).hidden = t !== name;
  });
  if (name === "analytics") loadAnalytics();
}

TABS.forEach((t) => {
  $(`#tab-${t}`).addEventListener("click", () => showTab(t));
});

// ── Settings / Tesseract health check ─────────────────────────────────────────

async function checkSettings() {
  try {
    const res = await fetch("/api/settings");
    const data = await res.json();
    const warn = $("#tesseractWarning");
    if (!data.tesseract?.available) {
      $("#tesseractWarningText").textContent =
        data.tesseract?.warning || "Tesseract not configured — OCR captures will not work.";
      warn.hidden = false;
    } else {
      warn.hidden = true;
    }
  } catch {
    // If settings endpoint fails, don't crash the dashboard
  }
}

// ── Overview ──────────────────────────────────────────────────────────────────

async function loadAccounts() {
  const res = await fetch("/api/accounts");
  const accounts = await res.json();
  renderGrid(accounts);
  renderRecommendation(accounts);
  populateAccountSelect(accounts);
}

function renderRecommendation(accounts) {
  const withScore = accounts.filter((a) => a.latest);
  const box = $("#recommendation");
  if (!withScore.length) { box.hidden = true; return; }
  const best = withScore[0];
  box.hidden = false;
  $("#recName").textContent = best.displayName;
  // pct values are already "% remaining" — use directly, no 100-inversion.
  const weeklyRemaining = best.latest.claude_weekly_pct;
  const fhRemaining     = best.latest.claude_fivehour_pct;
  $("#recWhy").textContent =
    `${Math.round(fhRemaining)}% five-hour and ${Math.round(weeklyRemaining)}% weekly remaining — most headroom for a full session right now.`;
}

function renderGrid(accounts) {
  const grid = $("#grid");
  const empty = $("#emptyState");
  grid.innerHTML = "";

  if (!accounts.length) { empty.hidden = false; return; }
  empty.hidden = true;

  accounts.forEach((acct, idx) => {
    const card = document.createElement("div");
    card.className = "card";
    card.addEventListener("click", () => openDetail(acct));

    const l = acct.latest;
    const stale = l ? isStale(l.timestamp_utc) : true;

    // Show email ID only when it differs from displayName (i.e. no custom name set)
    const emailSub = acct.id !== acct.displayName
      ? `<div class="card-email">${escapeHtml(acct.id)}</div>`
      : `<div class="card-email">${escapeHtml(acct.id)}</div>`;

    // Last captured time
    const capturedAgo = l?.timestamp_utc
      ? `<span class="card-captured">captured ${timeUntil(l.timestamp_utc)} ago</span>`
      : `<span class="card-captured">no reading yet</span>`;

    const geminiSection = (l?.gemini_weekly_pct != null || l?.gemini_fivehour_pct != null) ? `
      <div class="section-divider"></div>
      <div class="ring-readout-row">
        <span class="label">Gemini — Weekly</span>
        <span class="value">${fmtPct(l?.gemini_weekly_pct)}</span>
      </div>
      <div class="bar-track"><div class="bar-fill gemini" style="width:${l?.gemini_weekly_pct ?? 0}%"></div></div>
      <div class="ring-readout-row">
        <span class="label">Gemini — 5hr</span>
        <span class="value">${fmtPct(l?.gemini_fivehour_pct)}</span>
      </div>
      <div class="bar-track"><div class="bar-fill gemini-fh" style="width:${l?.gemini_fivehour_pct ?? 0}%"></div></div>
    ` : "";

    card.innerHTML = `
      <div class="card-top">
        <div>
          <div class="card-name">${escapeHtml(acct.displayName)}</div>
          ${emailSub}
        </div>
        <div class="card-rank">#${idx + 1}</div>
      </div>
      <div class="ring-readout">
        <div class="section-label">Claude / GPT</div>
        <div class="ring-readout-row">
          <span class="label">Weekly remaining</span>
          <span class="value">${fmtPct(l?.claude_weekly_pct)}</span>
        </div>
        <div class="bar-track"><div class="bar-fill ${barClass(l?.claude_weekly_pct, "claude")}" style="width:${l?.claude_weekly_pct ?? 0}%"></div></div>
        <div class="ring-readout-row">
          <span class="label">5-hour remaining</span>
          <span class="value">${fmtPct(l?.claude_fivehour_pct)}</span>
        </div>
        <div class="bar-track"><div class="bar-fill ${barClass(l?.claude_fivehour_pct, "claude-fh")}" style="width:${l?.claude_fivehour_pct ?? 0}%"></div></div>
        ${geminiSection}
      </div>
      <div class="card-footer">
        <div class="card-reset-row">
          <span class="reset-label">5-hour resets in</span>
          <span class="reset-value">${l?.claude_fivehour_reset_at ? timeUntil(l.claude_fivehour_reset_at) : "—"}</span>
        </div>
        <div class="card-reset-row">
          <span class="reset-label">Weekly resets in</span>
          <span class="reset-value">${l?.claude_weekly_reset_at ? timeUntil(l.claude_weekly_reset_at) : "—"}</span>
        </div>
        ${stale ? `<div class="stale-tag">stale · captured ${timeUntil(l?.timestamp_utc)} ago</div>` : ""}
      </div>
    `;
    grid.appendChild(card);
  });
}

// ── Detail overlay ────────────────────────────────────────────────────────────

async function openDetail(acct) {
  const res = await fetch(`/api/accounts/${encodeURIComponent(acct.id)}/history`);
  const history = await res.json();
  $("#detailTitle").textContent = acct.displayName;
  drawDetailChart(history);
  $("#detailOverlay").hidden = false;
}

function drawDetailChart(history) {
  const svg = $("#detailChart");
  svg.innerHTML = "";
  if (!history.length) return;

  const W = 640, H = 220, PAD = 20;
  const times = history.map((h) => new Date(h.timestamp_utc).getTime());
  const minT = Math.min(...times), maxT = Math.max(...times) || minT + 1;

  const x = (t) => PAD + ((t - minT) / (maxT - minT || 1)) * (W - 2 * PAD);
  const y = (pct) => H - PAD - (pct / 100) * (H - 2 * PAD);

  function pathFor(key) {
    return history
      .filter((h) => h[key] != null)
      .map((h, i) => `${i === 0 ? "M" : "L"}${x(new Date(h.timestamp_utc).getTime()).toFixed(1)},${y(h[key]).toFixed(1)}`)
      .join(" ");
  }

  const series = [
    { key: "claude_weekly_pct",  color: "var(--claude)" },
    { key: "claude_fivehour_pct", color: "var(--claude-fh)" },
    { key: "gemini_weekly_pct",  color: "var(--gemini)" },
  ];

  series.forEach(({ key, color }) => {
    const d = pathFor(key);
    if (!d) return;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", color);
    path.setAttribute("stroke-width", "2");
    path.setAttribute("stroke-linecap", "round");
    svg.appendChild(path);
  });
}

$("#closeDetail").addEventListener("click", () => { $("#detailOverlay").hidden = true; });
$("#refreshBtn").addEventListener("click", loadAccounts);

// ── Capture tab ───────────────────────────────────────────────────────────────

let _capturedBlob = null;
let _capturedMime = "image/png";

function populateAccountSelect(accounts) {
  const sel = $("#accountSelect");
  // Keep the first placeholder option, rebuild the rest
  sel.innerHTML = `<option value="">— select or type new —</option>
    <option value="__new__">+ New account…</option>`;
  accounts.forEach((a) => {
    const opt = document.createElement("option");
    opt.value = a.id;
    opt.textContent = a.displayName;
    sel.appendChild(opt);
  });
}

function showImagePreview(blob, mime) {
  _capturedBlob = blob;
  _capturedMime = mime;
  const url = URL.createObjectURL(blob);
  const img = $("#imagePreview");
  img.src = url;
  img.hidden = false;
  // Auto-run OCR
  runOcr(blob, mime);
}

async function runOcr(blob, mime) {
  const card = $("#ocrCard");
  const status = $("#ocrStatus");
  card.hidden = false;
  status.className = "ocr-status loading";
  status.textContent = "Running OCR…";

  const fd = new FormData();
  fd.append("image", blob, `screenshot.${mime.includes("png") ? "png" : "jpg"}`);

  try {
    const res = await fetch("/api/ocr", { method: "POST", body: fd });
    const data = await res.json();

    if (!res.ok) {
      status.className = "ocr-status error";
      status.textContent = data.error || "OCR failed.";
      if (data.detail) status.title = data.detail;
      return;
    }

    // Populate all six fields from OCR response
    const cg = data.claudeGpt || {};
    const gm = data.gemini || {};
    $("#f-claude-weekly").value          = cg.weeklyPct ?? "";
    $("#f-claude-5hr").value             = cg.fiveHourPct ?? "";
    $("#f-claude-reset").value           = cg.weeklyResetRaw ?? "";
    $("#f-claude-fivehour-reset").value  = cg.fiveHourResetRaw ?? "";
    $("#f-gemini-weekly").value          = gm.weeklyPct ?? "";
    $("#f-gemini-5hr").value             = gm.fiveHourPct ?? "";
    $("#f-gemini-reset").value           = gm.weeklyResetRaw ?? "";
    $("#f-gemini-fivehour-reset").value  = gm.fiveHourResetRaw ?? "";

    if (data.confidence === "low") {
      status.className = "ocr-status warn";
      status.textContent = "⚠ Low confidence — some numbers may be wrong. Check before saving.";
    } else {
      status.className = "ocr-status ok";
      status.textContent = "✓ OCR complete — verify the numbers below before saving.";
    }
  } catch (err) {
    status.className = "ocr-status error";
    status.textContent = "Network error: " + err.message;
  }
}

// Paste event
const pasteZone = $("#pasteZone");
document.addEventListener("paste", (e) => {
  const items = [...(e.clipboardData?.items || [])];
  const imgItem = items.find((it) => it.type.startsWith("image/"));
  if (!imgItem) return;
  const blob = imgItem.getAsFile();
  showImagePreview(blob, imgItem.type);
  showTab("capture");
});

// Click to focus paste zone
pasteZone.addEventListener("click", () => pasteZone.focus());
pasteZone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") pasteZone.focus();
});

// Drag-and-drop
pasteZone.addEventListener("dragover", (e) => { e.preventDefault(); pasteZone.classList.add("drag-over"); });
pasteZone.addEventListener("dragleave", () => pasteZone.classList.remove("drag-over"));
pasteZone.addEventListener("drop", (e) => {
  e.preventDefault();
  pasteZone.classList.remove("drag-over");
  const file = e.dataTransfer?.files?.[0];
  if (file && file.type.startsWith("image/")) showImagePreview(file, file.type);
});

// File input
$("#fileInput").addEventListener("change", (e) => {
  const file = e.target.files?.[0];
  if (file) showImagePreview(file, file.type);
});

// Account selector — show new-account input when "+ New" picked
$("#accountSelect").addEventListener("change", () => {
  $("#newAccountRow").hidden = $("#accountSelect").value !== "__new__";
});

// Save reading
$("#saveReadingBtn").addEventListener("click", async () => {
  const status = $("#saveStatus");
  let accountId = $("#accountSelect").value;
  if (accountId === "__new__") accountId = $("#newAccountId").value.trim();
  if (!accountId) {
    status.className = "save-status error";
    status.textContent = "Please select or enter an account ID.";
    return;
  }

  const claudeWeekly = parseFloat($("#f-claude-weekly").value);
  if (isNaN(claudeWeekly)) {
    status.className = "save-status error";
    status.textContent = "Claude/GPT weekly % is required.";
    return;
  }

  const body = {
    accountId,
    timestampUtc: new Date().toISOString(),
    claudeGpt: {
      weeklyPct:        claudeWeekly,
      fiveHourPct:      parseFloat($("#f-claude-5hr").value) || 0,
      weeklyResetRaw:   $("#f-claude-reset").value.trim(),
      fiveHourResetRaw: $("#f-claude-fivehour-reset").value.trim(),
    },
  };

  const gWeekly = parseFloat($("#f-gemini-weekly").value);
  if (!isNaN(gWeekly)) {
    body.gemini = {
      weeklyPct:        gWeekly,
      fiveHourPct:      parseFloat($("#f-gemini-5hr").value) || 0,
      weeklyResetRaw:   $("#f-gemini-reset").value.trim(),
      fiveHourResetRaw: $("#f-gemini-fivehour-reset").value.trim(),
    };
  }

  status.className = "save-status loading";
  status.textContent = "Saving…";

  try {
    const res = await fetch("/api/readings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) {
      status.className = "save-status error";
      status.textContent = data.error || "Save failed.";
    } else {
      status.className = "save-status ok";
      status.textContent = "✓ Saved! Returning to Overview in 2s…";
      await loadAccounts();
      setTimeout(() => showTab("overview"), 2000);
    }
  } catch (err) {
    status.className = "save-status error";
    status.textContent = "Network error: " + err.message;
  }
});

// ── Analytics tab ─────────────────────────────────────────────────────────────

let _burnChart = null;
let _currentRange = "week";
let _analyticsData = null;
let _activeAccounts = new Set();

async function loadAnalytics() {
  try {
    const res = await fetch(`/api/analytics?range=${_currentRange}`);
    _analyticsData = await res.json();
    renderAnalytics(_analyticsData);
  } catch (err) {
    console.error("Analytics load failed", err);
  }
}

function renderAnalytics(data) {
  if (!data) return;
  renderProjection(data);
  renderSessionCount(data);
  renderAccountFilters(data);
  renderBurnChart(data);
  renderHeatmap(data);
  renderEfficiency(data);
}

function renderProjection(data) {
  const el = $("#projectionText");
  if (data.daysRemaining == null) {
    el.textContent = "Not enough data (need ≥2 readings per account in last 7 days)";
  } else if (data.daysRemaining <= 0) {
    el.textContent = "⚠ One or more accounts may already be at capacity.";
  } else {
    el.textContent = `At current pace, all accounts' weekly quota exhausted in ~${Math.round(data.daysRemaining)} day${Math.round(data.daysRemaining) === 1 ? "" : "s"}.`;
  }
}

function renderSessionCount(data) {
  $("#sessionCount").textContent = data.sessionCount
    ? `${data.sessionCount} session${data.sessionCount === 1 ? "" : "s"}`
    : "0 sessions";
}

function renderAccountFilters(data) {
  const container = $("#accountFilters");
  container.innerHTML = "";
  if (!data.series?.length) return;

  // Init active set from accounts that have data
  if (_activeAccounts.size === 0) {
    data.series.filter((s) => s.points.length).forEach((s) => _activeAccounts.add(s.accountId));
  }

  data.series.forEach((s) => {
    const label = document.createElement("label");
    label.className = "filter-chip";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = _activeAccounts.has(s.accountId);
    cb.addEventListener("change", () => {
      if (cb.checked) _activeAccounts.add(s.accountId);
      else _activeAccounts.delete(s.accountId);
      renderBurnChart(_analyticsData);
    });
    label.appendChild(cb);
    label.appendChild(document.createTextNode(s.displayName));
    container.appendChild(label);
  });
}

const ACCOUNT_COLORS = [
  "rgba(79,209,197,1)", "rgba(155,135,245,1)", "rgba(240,180,41,1)",
  "rgba(127,227,218,1)", "rgba(229,72,77,1)", "rgba(100,220,140,1)",
];

function renderBurnChart(data) {
  const ctx = $("#burnChart").getContext("2d");
  if (_burnChart) { _burnChart.destroy(); _burnChart = null; }

  const visible = (data.series || []).filter(
    (s) => s.points.length && (_activeAccounts.size === 0 || _activeAccounts.has(s.accountId))
  );

  if (!visible.length) return;

  const datasets = visible.map((s, i) => ({
    label: s.displayName,
    data: s.points.map((p) => ({ x: p.day, y: p.claudeWeeklyPct })),
    borderColor: ACCOUNT_COLORS[i % ACCOUNT_COLORS.length],
    backgroundColor: ACCOUNT_COLORS[i % ACCOUNT_COLORS.length].replace(",1)", ",0.08)"),
    borderWidth: 2,
    pointRadius: 3,
    tension: 0.3,
    fill: false,
  }));

  _burnChart = new Chart(ctx, {
    type: "line",
    data: { datasets },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: {
          type: "category",
          ticks: { color: "#8890a6", font: { family: "IBM Plex Mono", size: 11 } },
          grid: { color: "rgba(42,52,80,0.6)" },
        },
        y: {
          min: 0, max: 100,
          title: { display: true, text: "% remaining", color: "#8890a6", font: { size: 11 } },
          ticks: {
            color: "#8890a6",
            font: { family: "IBM Plex Mono", size: 11 },
            callback: (v) => v + "%",
          },
          grid: { color: "rgba(42,52,80,0.6)" },
        },
      },
      plugins: {
        legend: {
          labels: { color: "#e7eaf3", font: { family: "Inter", size: 12 } },
        },
        tooltip: {
          callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y?.toFixed(1)}% remaining` },
        },
      },
    },
  });
}

function renderHeatmap(data) {
  const grid = $("#heatmapGrid");
  grid.innerHTML = "";
  if (!data.heatmap?.length) { grid.textContent = "No data"; return; }

  const maxPct = Math.max(...data.heatmap.map((d) => d.avgPct ?? 0), 1);

  data.heatmap.forEach((d) => {
    const cell = document.createElement("div");
    cell.className = "heatmap-cell";
    const intensity = d.avgPct != null ? d.avgPct / maxPct : 0;
    cell.style.setProperty("--intensity", intensity.toFixed(3));
    cell.innerHTML = `
      <div class="heatmap-label">${d.label}</div>
      <div class="heatmap-value">${d.avgPct != null ? Math.round(d.avgPct) + "%" : "—"}</div>
    `;
    cell.title = `${d.label}: ${d.count} reading${d.count !== 1 ? "s" : ""}`;
    grid.appendChild(cell);
  });
}

function renderEfficiency(data) {
  const list = $("#efficiencyList");
  list.innerHTML = "";
  const accounts = (data.series || []).filter((s) => s.points.length >= 2);
  if (!accounts.length) { list.textContent = "Not enough readings for efficiency stats."; return; }

  accounts.forEach((s) => {
    const consumedPerReading = [];
    for (let i = 1; i < s.points.length; i++) {
      // pct = remaining; consumption = decrease in remaining between readings
      const delta = s.points[i - 1].claudeWeeklyPct - s.points[i].claudeWeeklyPct;
      if (delta > 0) consumedPerReading.push(delta);
    }
    const avg = consumedPerReading.length
      ? consumedPerReading.reduce((a, b) => a + b, 0) / consumedPerReading.length
      : null;

    const row = document.createElement("div");
    row.className = "efficiency-row";
    row.innerHTML = `
      <span class="efficiency-name">${escapeHtml(s.displayName)}</span>
      <span class="efficiency-stat">${avg != null ? `~${avg.toFixed(1)}% consumed per reading` : "insufficient data"}</span>
    `;
    list.appendChild(row);
  });
}

// Range toggle
$$(".range-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".range-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    _currentRange = btn.dataset.range;
    _activeAccounts.clear();
    loadAnalytics();
  });
});

// ── Boot ──────────────────────────────────────────────────────────────────────

checkSettings();
loadAccounts();
setInterval(loadAccounts, 60000);
setInterval(checkSettings, 30000); // re-check tesseract availability periodically
