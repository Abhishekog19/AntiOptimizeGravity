const $ = (sel) => document.querySelector(sel);

function fmtPct(v) {
  return v == null ? "—" : `${Math.round(v)}%`;
}

function timeUntil(iso) {
  if (!iso) return "unknown";
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "due now";
  const hrs = ms / 3600000;
  if (hrs < 1) return `${Math.round(ms / 60000)}m`;
  if (hrs < 48) return `${Math.round(hrs)}h`;
  return `${Math.round(hrs / 24)}d`;
}

function barClass(pct, base) {
  if (pct == null) return base;
  if (pct >= 90) return "danger";
  if (pct >= 70) return "warn";
  return base;
}

function isStale(iso) {
  if (!iso) return true;
  return Date.now() - new Date(iso).getTime() > 30 * 60 * 1000; // 30 min
}

async function loadAccounts() {
  const res = await fetch("/api/accounts");
  const accounts = await res.json();
  renderGrid(accounts);
  renderRecommendation(accounts);
}

function renderRecommendation(accounts) {
  const withScore = accounts.filter((a) => a.latest);
  const box = $("#recommendation");
  if (!withScore.length) {
    box.hidden = true;
    return;
  }
  const best = withScore[0];
  box.hidden = false;
  $("#recName").textContent = best.displayName;
  const weeklyRemaining = 100 - best.latest.claude_weekly_pct;
  const fhRemaining = 100 - best.latest.claude_fivehour_pct;
  $("#recWhy").textContent =
    `${Math.round(fhRemaining)}% five-hour and ${Math.round(weeklyRemaining)}% weekly remaining — most headroom for a full session right now.`;
}

function renderGrid(accounts) {
  const grid = $("#grid");
  const empty = $("#emptyState");
  grid.innerHTML = "";

  if (!accounts.length) {
    empty.hidden = false;
    return;
  }
  empty.hidden = true;

  accounts.forEach((acct, idx) => {
    const card = document.createElement("div");
    card.className = "card";
    card.addEventListener("click", () => openDetail(acct));

    const l = acct.latest;
    const stale = l ? isStale(l.timestamp_utc) : true;

    card.innerHTML = `
      <div class="card-top">
        <div class="card-name">${escapeHtml(acct.displayName)}</div>
        <div class="card-rank">#${idx + 1}</div>
      </div>
      <div class="ring-readout">
        <div class="ring-readout-row">
          <span class="label">Claude/GPT — Weekly</span>
          <span class="value">${fmtPct(l?.claude_weekly_pct)}</span>
        </div>
        <div class="bar-track"><div class="bar-fill ${barClass(l?.claude_weekly_pct, "claude")}" style="width:${l?.claude_weekly_pct ?? 0}%"></div></div>
        <div class="ring-readout-row">
          <span class="label">Claude/GPT — 5hr</span>
          <span class="value">${fmtPct(l?.claude_fivehour_pct)}</span>
        </div>
        <div class="bar-track"><div class="bar-fill ${barClass(l?.claude_fivehour_pct, "claude-fh")}" style="width:${l?.claude_fivehour_pct ?? 0}%"></div></div>
        ${l?.gemini_weekly_pct != null ? `
        <div class="ring-readout-row">
          <span class="label">Gemini — Weekly</span>
          <span class="value">${fmtPct(l?.gemini_weekly_pct)}</span>
        </div>
        <div class="bar-track"><div class="bar-fill gemini" style="width:${l?.gemini_weekly_pct ?? 0}%"></div></div>
        ` : ""}
      </div>
      <div class="card-footer">
        <span>5hr reset in ${l ? timeUntil(l.claude_fivehour_reset_at) : "—"}</span>
        <span class="${stale ? "stale-tag" : ""}">${stale ? "stale" : "live"} · ${l ? timeUntil(l.claude_weekly_reset_at) : "—"} to weekly reset</span>
      </div>
    `;
    grid.appendChild(card);
  });
}

async function openDetail(acct) {
  const res = await fetch(`/api/accounts/${encodeURIComponent(acct.id)}/history`);
  const history = await res.json();
  $("#detailTitle").textContent = acct.displayName;
  drawChart(history);
  $("#detailOverlay").hidden = false;
}

function drawChart(history) {
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
    { key: "claude_weekly_pct", color: "var(--claude)" },
    { key: "claude_fivehour_pct", color: "var(--claude-fh)" },
    { key: "gemini_weekly_pct", color: "var(--gemini)" },
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

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

$("#closeDetail").addEventListener("click", () => { $("#detailOverlay").hidden = true; });
$("#refreshBtn").addEventListener("click", loadAccounts);

function tickClock() {
  $("#clock").textContent = new Date().toUTCString().slice(17, 25) + " UTC";
}
tickClock();
setInterval(tickClock, 1000);

loadAccounts();
setInterval(loadAccounts, 60000);
