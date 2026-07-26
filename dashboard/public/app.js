// ─────────────────────────────────────────────────────────────────────────────
//  Antigravity Quota Dashboard — app.js  (UI Revamp v2)
// ─────────────────────────────────────────────────────────────────────────────

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtPct(v) {
  return v == null ? '—' : `${Math.round(v)}%`;
}

/** Time remaining until iso timestamp — used for reset countdowns. */
function timeUntil(iso) {
  if (!iso) return '—';
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return 'now';
  const totalMins = Math.floor(ms / 60000);
  const mins = totalMins % 60;
  const hrs  = Math.floor(totalMins / 60) % 24;
  const days = Math.floor(totalMins / 1440);
  if (days > 0 && hrs > 0)  return `${days}d ${hrs}h`;
  if (days > 0)              return `${days}d`;
  if (hrs  > 0 && mins > 0) return `${hrs}h ${mins}m`;
  if (hrs  > 0)              return `${hrs}h`;
  return `${mins}m`;
}

/** Elapsed time since iso timestamp — used for "last used X ago", "refreshed X ago". */
function timeAgo(iso) {
  if (!iso) return '—';
  const ms = Date.now() - new Date(iso).getTime();
  if (ms <= 0) return 'just now';
  const totalMins = Math.floor(ms / 60000);
  const mins = totalMins % 60;
  const hrs  = Math.floor(totalMins / 60) % 24;
  const days = Math.floor(totalMins / 1440);
  if (days > 0 && hrs > 0)  return `${days}d ${hrs}h`;
  if (days > 0)              return `${days}d`;
  if (hrs  > 0 && mins > 0) return `${hrs}h ${mins}m`;
  if (hrs  > 0)              return `${hrs}h`;
  if (totalMins > 0)         return `${totalMins}m`;
  return 'just now';
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
  );
}

// ── Pool state logic ──────────────────────────────────────────────────────────

/**
 * Determine display state for a single quota pool.
 *
 * Returns one of:
 *   { state: 'never_captured' }
 *   { state: 'auto_refreshed', resetAt, capturedAt }
 *   { state: 'recent_snapshot', pct, capturedAt }
 *
 * active_now is a card-level concept (cross-account comparison), handled separately.
 *
 * Edge cases confirmed:
 *   - resetAt null/falsy with a real reading (e.g. Gemini at 100%, no countdown):
 *     `if (resetAt && ...)` → falsy short-circuits → falls through to recent_snapshot. ✓
 *   - capturedAt null only when pct is also null (never_captured path). ✓
 */
function getPoolDisplayState(pct, resetAt, capturedAt) {
  if (pct == null || capturedAt == null) {
    return { state: 'never_captured' };
  }
  const now = Date.now();
  if (resetAt && now > new Date(resetAt).getTime()) {
    return { state: 'auto_refreshed', resetAt, capturedAt };
  }
  return { state: 'recent_snapshot', pct, capturedAt };
}

/**
 * Return display-corrected percentage for recommendation scoring.
 *
 * Invariant: each state maps to exactly one numeric value.
 *   auto_refreshed  → 100          Pool has reset; fully available.
 *   recent_snapshot → poolState.pct Real captured value; never fabricated.
 *   never_captured  → 0            Pessimistic; prevents unknown from outranking real data.
 *
 * The recent_snapshot → poolState.pct mapping is explicit so an account with
 * 40% real data scores 40, never 100. Only auto_refreshed receives 100.
 */
function getEffectivePct(poolState) {
  switch (poolState.state) {
    case 'auto_refreshed':  return 100;
    case 'recent_snapshot': return poolState.pct;
    default:                return 0;   // never_captured
  }
}

/**
 * Semantic color class from percentage remaining.
 * Thresholds: green ≥50% · amber 20–49% · red <20%
 */
function poolColorClass(pct) {
  if (pct < 20) return 'danger';
  if (pct < 50) return 'warn';
  return 'ok';
}

/**
 * Bar fill CSS class.
 * Uses semantic color when warn/danger; falls back to brand color when healthy (≥50%).
 * poolType: 'claude' | 'claude-fh' | 'gemini' | 'gemini-fh'
 */
function poolBarClass(pct, poolType) {
  if (pct < 20) return 'bar-danger';
  if (pct < 50) return 'bar-warn';
  return `bar-${poolType}`;
}

// ── Render single pool section — returns HTML string ──────────────────────────

function renderPool(label, poolState, poolType) {
  switch (poolState.state) {

    case 'never_captured':
      return `
        <div class="pool-row">
          <span class="pool-label">${label}</span>
          <span class="pool-value pool-value--unknown">—</span>
        </div>
        <div class="bar-placeholder">
          <span class="bar-placeholder-text">Not yet tracked</span>
        </div>`;

    case 'auto_refreshed': {
      const when = timeAgo(poolState.resetAt);
      return `
        <div class="pool-row">
          <span class="pool-label">${label}</span>
          <span class="pool-value pool-value--ok">100%</span>
        </div>
        <div class="bar-track"><div class="bar-fill bar-ok" style="width:100%"></div></div>
        <div class="pool-secondary pool-secondary--refreshed">↺ refreshed ${when} ago</div>`;
    }

    case 'recent_snapshot': {
      const pct    = poolState.pct;
      const cls    = poolColorClass(pct);
      const barCls = poolBarClass(pct, poolType);
      return `
        <div class="pool-row">
          <span class="pool-label">${label}</span>
          <span class="pool-value pool-value--${cls}">${Math.round(pct)}%</span>
        </div>
        <div class="bar-track"><div class="bar-fill ${barCls}" style="width:${pct}%"></div></div>`;
    }

    default: return '';
  }
}

// ── Active account detection ──────────────────────────────────────────────────

/** Returns the account ID with the single most-recent capture across all accounts. */
function findActiveAccountId(accounts) {
  let maxTime = -Infinity;
  let activeId = null;
  accounts.forEach((a) => {
    if (a.latest?.timestamp_utc) {
      const t = new Date(a.latest.timestamp_utc).getTime();
      if (t > maxTime) { maxTime = t; activeId = a.id; }
    }
  });
  return activeId;
}

// ── Recommendation scoring ────────────────────────────────────────────────────

/**
 * Score an account using display-corrected pool values.
 * Formula matches backend: min(five_hour_remaining, weekly_remaining / 33 * 100)
 * Returns -1 for accounts with no readings.
 */
function accountScore(acct) {
  const l = acct.latest;
  if (!l) return -1;
  const cwState = getPoolDisplayState(l.claude_weekly_pct,    l.claude_weekly_reset_at,    l.timestamp_utc);
  const cfState = getPoolDisplayState(l.claude_fivehour_pct,  l.claude_fivehour_reset_at,  l.timestamp_utc);
  const wEff = getEffectivePct(cwState);
  const fEff = getEffectivePct(cfState);
  return Math.min(fEff, (wEff / 33) * 100);
}

// ── Build full card HTML — returns HTML string ────────────────────────────────

function buildCard(acct, rank, isRecommended, isActive) {
  const l = acct.latest;

  const classes = ['card',
    isRecommended ? 'card--recommended' : '',
    isActive      ? 'card--active-now'  : '',
  ].filter(Boolean).join(' ');

  const badge = isActive
    ? `<span class="active-now-badge">● Active now</span>`
    : `<span class="card-rank">#${rank}</span>`;

  const email = `<div class="card-email">${escapeHtml(acct.id)}</div>`;

  if (!l) {
    return `
      <div class="${classes}" data-account-id="${escapeHtml(acct.id)}">
        <div class="card-top">
          <div>
            <div class="card-name">${escapeHtml(acct.displayName)}</div>
            ${email}
          </div>
          ${badge}
        </div>
        <div class="bar-placeholder"><span class="bar-placeholder-text">No readings yet</span></div>
      </div>`;
  }

  // Compute pool states for all 4 pools
  const cwState = getPoolDisplayState(l.claude_weekly_pct,   l.claude_weekly_reset_at,   l.timestamp_utc);
  const cfState = getPoolDisplayState(l.claude_fivehour_pct, l.claude_fivehour_reset_at, l.timestamp_utc);
  const gwState = getPoolDisplayState(l.gemini_weekly_pct,   l.gemini_weekly_reset_at,   l.timestamp_utc);
  const gfState = getPoolDisplayState(l.gemini_fivehour_pct, l.gemini_fivehour_reset_at, l.timestamp_utc);

  const hasGemini = l.gemini_weekly_pct != null || l.gemini_fivehour_pct != null;

  const weeklyReset = l.claude_weekly_reset_at   ? timeUntil(l.claude_weekly_reset_at)   : '—';
  const fiveHrReset = l.claude_fivehour_reset_at ? timeUntil(l.claude_fivehour_reset_at) : '—';
  const lastUsedStr = l.timestamp_utc ? `last used ${timeAgo(l.timestamp_utc)} ago` : '';

  const geminiSection = hasGemini ? `
    <div class="section-divider"></div>
    <div class="section-label" style="color:var(--gemini)">Gemini</div>
    ${renderPool('Weekly', gwState, 'gemini')}
    ${renderPool('5-hour', gfState, 'gemini-fh')}` : '';

  return `
    <div class="${classes}" data-account-id="${escapeHtml(acct.id)}">
      <div class="card-top">
        <div>
          <div class="card-name">${escapeHtml(acct.displayName)}</div>
          ${email}
        </div>
        ${badge}
      </div>

      <div class="section-label">Claude / GPT</div>
      ${renderPool('Weekly', cwState, 'claude')}
      ${renderPool('5-hour', cfState, 'claude-fh')}
      ${geminiSection}

      <div class="card-footer">
        <div class="card-reset-row">
          <span class="reset-label">5-hr resets in</span>
          <span class="reset-value">${fiveHrReset}</span>
        </div>
        <div class="card-reset-row">
          <span class="reset-label">Weekly resets in</span>
          <span class="reset-value">${weeklyReset}</span>
        </div>
        ${lastUsedStr ? `<div class="card-captured">${lastUsedStr}</div>` : ''}
      </div>
    </div>`;
}

// ── Mode detection ────────────────────────────────────────────────────────────

let _currentMode  = '';
let _lastAccounts = [];

function getMode() {
  const w = window.innerWidth;
  if (w <  420) return 'glance';
  if (w <  800) return 'compact';
  if (w < 1200) return 'standard';
  return 'full';
}

function updateMode() {
  const mode = getMode();
  if (mode === _currentMode) return;
  _currentMode = mode;
  document.body.className = `mode-${mode}`;
  // Re-render with current data if available — different mode may need different layout
  if (_lastAccounts.length) renderOverview(_lastAccounts);
}

window.addEventListener('resize', updateMode);

// ── Clock ─────────────────────────────────────────────────────────────────────

function tickClock() {
  $('#clock').textContent = new Date().toUTCString().slice(17, 25) + ' UTC';
}
tickClock();
setInterval(tickClock, 1000);

// ── Tab switching ─────────────────────────────────────────────────────────────

const TABS = ['overview', 'analytics'];

function showTab(name) {
  TABS.forEach((t) => {
    const btn = $(`#tab-${t}`);
    if (!btn) return;
    btn.classList.toggle('active', t === name);
    btn.setAttribute('aria-selected', t === name ? 'true' : 'false');
    $(`#pane-${t}`).hidden = t !== name;
  });
  if (name === 'analytics') loadAnalytics();
}

TABS.forEach((t) => {
  const btn = $(`#tab-${t}`);
  if (btn) btn.addEventListener('click', () => showTab(t));
});

// ── Overview ──────────────────────────────────────────────────────────────────

async function loadAccounts() {
  const res      = await fetch('/api/accounts');
  const accounts = await res.json();
  _lastAccounts  = accounts;
  renderOverview(accounts);
}

function renderOverview(accounts) {
  const empty = $('#emptyState');

  if (!accounts.length) {
    empty.hidden = true;    // Let the paragraph handle empty state
    $('#heroCard').innerHTML    = '';
    $('#compactList').innerHTML = '';
    $('#grid').innerHTML        = '';
    empty.hidden = false;
    return;
  }
  empty.hidden = true;

  // Sort best → worst by display-corrected score
  const sorted      = [...accounts].sort((a, b) => accountScore(b) - accountScore(a));
  const recommended = sorted[0];
  const activeId    = findActiveAccountId(accounts);

  if (_currentMode === 'glance') {
    renderHeroCard(recommended, activeId);
    renderCompactList(sorted.slice(1), activeId);
    $('#grid').innerHTML = '';
  } else {
    $('#heroCard').innerHTML    = '';
    $('#compactList').innerHTML = '';
    renderGrid(sorted, recommended.id, activeId);
  }

  // Analytics strip — only fetched and rendered at standard/full
  if (_currentMode === 'standard' || _currentMode === 'full') {
    renderAnalyticsStrip();
  }
}

// ── Hero card  (Glance mode) ──────────────────────────────────────────────────

function renderHeroCard(acct, activeId) {
  const hero     = $('#heroCard');
  const l        = acct.latest;
  const isActive = acct.id === activeId;

  const activeLine = isActive
    ? `<div class="active-now-badge" style="margin-bottom:8px">● Active now</div>`
    : '';

  if (!l) {
    hero.innerHTML = `
      ${activeLine}
      <div class="hero-card-label">▶ Use next</div>
      <div class="hero-card-name">${escapeHtml(acct.displayName)}</div>
      <div class="hero-card-reason">No readings yet</div>`;
    return;
  }

  const cwState = getPoolDisplayState(l.claude_weekly_pct,   l.claude_weekly_reset_at,   l.timestamp_utc);
  const cfState = getPoolDisplayState(l.claude_fivehour_pct, l.claude_fivehour_reset_at, l.timestamp_utc);
  const wEff    = getEffectivePct(cwState);
  const fEff    = getEffectivePct(cfState);

  const reason      = `${Math.round(fEff)}% five-hour · ${Math.round(wEff)}% weekly remaining — most headroom for a full session`;
  const weeklyReset = l.claude_weekly_reset_at   ? timeUntil(l.claude_weekly_reset_at)   : '—';
  const fiveHrReset = l.claude_fivehour_reset_at ? timeUntil(l.claude_fivehour_reset_at) : '—';

  hero.innerHTML = `
    ${activeLine}
    <div class="hero-card-label">▶ Use next</div>
    <div class="hero-card-name">${escapeHtml(acct.displayName)}</div>
    <div class="hero-card-reason">${reason}</div>
    <div class="section-label">Claude / GPT</div>
    ${renderPool('Weekly', cwState, 'claude')}
    ${renderPool('5-hour', cfState, 'claude-fh')}
    <div class="card-footer" style="margin-top:8px;padding-top:8px">
      <div class="card-reset-row">
        <span class="reset-label">Weekly resets in</span>
        <span class="reset-value">${weeklyReset}</span>
      </div>
      <div class="card-reset-row">
        <span class="reset-label">5-hr resets in</span>
        <span class="reset-value">${fiveHrReset}</span>
      </div>
    </div>`;
}

// ── Compact list  (Glance mode — all non-recommended accounts) ────────────────

function renderCompactList(accounts, activeId) {
  const list = $('#compactList');

  list.innerHTML = accounts.map((acct) => {
    const l        = acct.latest;
    const isActive = acct.id === activeId;

    const cwState = l
      ? getPoolDisplayState(l.claude_weekly_pct,   l.claude_weekly_reset_at,   l.timestamp_utc)
      : { state: 'never_captured' };
    const cfState = l
      ? getPoolDisplayState(l.claude_fivehour_pct, l.claude_fivehour_reset_at, l.timestamp_utc)
      : { state: 'never_captured' };

    const wEff  = getEffectivePct(cwState);
    const fEff  = getEffectivePct(cfState);
    const wCls  = cwState.state === 'never_captured' ? 'pool-value--unknown' : `pool-value--${poolColorClass(wEff)}`;
    const fCls  = cfState.state === 'never_captured' ? 'pool-value--unknown' : `pool-value--${poolColorClass(fEff)}`;
    const wText = cwState.state === 'never_captured' ? '—' : `${Math.round(wEff)}%`;
    const fText = cfState.state === 'never_captured' ? '—' : `${Math.round(fEff)}%`;

    const rowCls = ['compact-row', isActive ? 'compact-row--active' : ''].filter(Boolean).join(' ');

    return `
      <div class="${rowCls}" data-account-id="${escapeHtml(acct.id)}">
        <span class="compact-row-name">${escapeHtml(acct.displayName)}</span>
        <span class="compact-row-pcts">
          <span class="${wCls}">${wText}</span>
          <span style="color:var(--hairline)"> · </span>
          <span class="${fCls}">${fText}</span>
        </span>
      </div>`;
  }).join('');

  list.querySelectorAll('.compact-row').forEach((row) => {
    row.addEventListener('click', () => {
      const acct = _lastAccounts.find((a) => a.id === row.dataset.accountId);
      if (acct) openDetail(acct);
    });
  });
}

// ── Account grid  (Compact / Standard / Full) ─────────────────────────────────

function renderGrid(sortedAccounts, recommendedId, activeId) {
  const grid = $('#grid');
  grid.innerHTML = sortedAccounts.map((acct, idx) =>
    buildCard(acct, idx + 1, acct.id === recommendedId, acct.id === activeId)
  ).join('');

  grid.querySelectorAll('.card').forEach((card) => {
    card.addEventListener('click', () => {
      const acct = _lastAccounts.find((a) => a.id === card.dataset.accountId);
      if (acct) openDetail(acct);
    });
  });
}

// ── Analytics strip  (Standard + Full — in overview pane) ────────────────────

let _stripInflight = false;

async function renderAnalyticsStrip() {
  const strip = $('#analyticsStrip');
  if (!strip || _stripInflight) return;
  _stripInflight = true;
  try {
    const res  = await fetch('/api/analytics?range=week');
    const data = await res.json();

    const sessions = data.sessionCount ?? 0;
    const days     = data.daysRemaining;

    let burnLine;
    if (days == null) {
      burnLine = 'Not enough data yet';
    } else if (days <= 0) {
      burnLine = '⚠ One or more accounts near capacity';
    } else {
      const d = Math.round(days);
      burnLine = `Quota exhausted in ~${d} day${d === 1 ? '' : 's'} at current pace`;
    }

    strip.innerHTML = `
      <div class="analytics-strip-item">
        <span class="analytics-strip-label">Sessions this week</span>
        <span class="analytics-strip-value">${sessions}</span>
      </div>
      <div class="analytics-strip-item">
        <span class="analytics-strip-label">Burn rate</span>
        <span class="analytics-strip-value">${burnLine}</span>
      </div>`;
  } catch {
    strip.innerHTML = ''; // fail silently
  } finally {
    _stripInflight = false;
  }
}

// ── Detail overlay ────────────────────────────────────────────────────────────

async function openDetail(acct) {
  const res     = await fetch(`/api/accounts/${encodeURIComponent(acct.id)}/history`);
  const history = await res.json();
  $('#detailTitle').textContent = acct.displayName;
  drawDetailChart(history);
  $('#detailOverlay').hidden = false;
}

function drawDetailChart(history) {
  const svg = $('#detailChart');
  svg.innerHTML = '';
  if (!history.length) return;

  const W = 640, H = 220, PAD = 20;
  const times = history.map((h) => new Date(h.timestamp_utc).getTime());
  const minT  = Math.min(...times);
  const maxT  = Math.max(...times) || minT + 1;

  const xFn = (t)   => PAD + ((t - minT) / (maxT - minT || 1)) * (W - 2 * PAD);
  const yFn = (pct) => H - PAD - (pct / 100) * (H - 2 * PAD);

  function pathFor(key) {
    return history
      .filter((h) => h[key] != null)
      .map((h, i) => {
        const xv = xFn(new Date(h.timestamp_utc).getTime()).toFixed(1);
        const yv = yFn(h[key]).toFixed(1);
        return `${i === 0 ? 'M' : 'L'}${xv},${yv}`;
      })
      .join(' ');
  }

  const series = [
    { key: 'claude_weekly_pct',   color: 'var(--claude)' },
    { key: 'claude_fivehour_pct', color: 'rgba(79,209,197,.6)' },
    { key: 'gemini_weekly_pct',   color: 'var(--gemini)' },
  ];

  series.forEach(({ key, color }) => {
    const d = pathFor(key);
    if (!d) return;
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', d);
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', color);
    path.setAttribute('stroke-width', '2');
    path.setAttribute('stroke-linecap', 'round');
    svg.appendChild(path);
  });
}

$('#closeDetail').addEventListener('click', () => { $('#detailOverlay').hidden = true; });
$('#refreshBtn').addEventListener('click', loadAccounts);

// ── Analytics tab ─────────────────────────────────────────────────────────────

let _burnChart     = null;
let _currentRange  = 'week';
let _analyticsData = null;
let _activeAccounts = new Set();

async function loadAnalytics() {
  try {
    const res  = await fetch(`/api/analytics?range=${_currentRange}`);
    _analyticsData = await res.json();
    renderAnalytics(_analyticsData);
  } catch (err) {
    console.error('Analytics load failed', err);
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
  const el = $('#projectionText');
  if (data.daysRemaining == null) {
    el.textContent = 'Not enough data (need ≥2 readings per account in last 7 days)';
  } else if (data.daysRemaining <= 0) {
    el.textContent = '⚠ One or more accounts may already be at capacity.';
  } else {
    const d = Math.round(data.daysRemaining);
    el.textContent = `At current pace, all accounts' weekly quota exhausted in ~${d} day${d === 1 ? '' : 's'}.`;
  }
}

function renderSessionCount(data) {
  $('#sessionCount').textContent = data.sessionCount
    ? `${data.sessionCount} session${data.sessionCount === 1 ? '' : 's'}`
    : '0 sessions';
}

function renderAccountFilters(data) {
  const container = $('#accountFilters');
  container.innerHTML = '';
  if (!data.series?.length) return;

  if (_activeAccounts.size === 0) {
    data.series.filter((s) => s.points.length).forEach((s) => _activeAccounts.add(s.accountId));
  }

  data.series.forEach((s) => {
    const label = document.createElement('label');
    label.className = 'filter-chip';
    const cb = document.createElement('input');
    cb.type    = 'checkbox';
    cb.checked = _activeAccounts.has(s.accountId);
    cb.addEventListener('change', () => {
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
  'rgba(79,209,197,1)',  'rgba(155,135,245,1)', 'rgba(240,180,41,1)',
  'rgba(127,227,218,1)', 'rgba(229,72,77,1)',   'rgba(100,220,140,1)',
];

function renderBurnChart(data) {
  // Charts only render in full mode (CSS hides the card in standard; guard here prevents wasted work)
  if (_currentMode !== 'full') return;

  const ctx = $('#burnChart').getContext('2d');
  if (_burnChart) { _burnChart.destroy(); _burnChart = null; }

  const visible = (data.series || []).filter(
    (s) => s.points.length && (_activeAccounts.size === 0 || _activeAccounts.has(s.accountId))
  );
  if (!visible.length) return;

  const datasets = visible.map((s, i) => ({
    label:           s.displayName,
    data:            s.points.map((p) => ({ x: p.day, y: p.claudeWeeklyPct })),
    borderColor:     ACCOUNT_COLORS[i % ACCOUNT_COLORS.length],
    backgroundColor: ACCOUNT_COLORS[i % ACCOUNT_COLORS.length].replace(',1)', ',0.08)'),
    borderWidth: 2,
    pointRadius: 3,
    tension: 0.3,
    fill: false,
  }));

  _burnChart = new Chart(ctx, {
    type: 'line',
    data: { datasets },
    options: {
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: {
          type: 'category',
          ticks: { color: '#8890a6', font: { family: 'IBM Plex Mono', size: 11 } },
          grid:  { color: 'rgba(42,52,80,.6)' },
        },
        y: {
          min: 0, max: 100,
          title: { display: true, text: '% remaining', color: '#8890a6', font: { size: 11 } },
          ticks: {
            color: '#8890a6',
            font:  { family: 'IBM Plex Mono', size: 11 },
            callback: (v) => v + '%',
          },
          grid: { color: 'rgba(42,52,80,.6)' },
        },
      },
      plugins: {
        legend: {
          labels: { color: '#e7eaf3', font: { family: 'Inter', size: 12 } },
        },
        tooltip: {
          callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y?.toFixed(1)}% remaining` },
        },
      },
    },
  });
}

function renderHeatmap(data) {
  const grid = $('#heatmapGrid');
  grid.innerHTML = '';
  if (!data.heatmap?.length) { grid.textContent = 'No data'; return; }

  const maxPct = Math.max(...data.heatmap.map((d) => d.avgPct ?? 0), 1);

  data.heatmap.forEach((d) => {
    const cell      = document.createElement('div');
    cell.className  = 'heatmap-cell';
    const intensity = d.avgPct != null ? d.avgPct / maxPct : 0;
    cell.style.setProperty('--intensity', intensity.toFixed(3));
    cell.innerHTML  = `
      <div class="heatmap-label">${d.label}</div>
      <div class="heatmap-value">${d.avgPct != null ? Math.round(d.avgPct) + '%' : '—'}</div>`;
    cell.title = `${d.label}: ${d.count} reading${d.count !== 1 ? 's' : ''}`;
    grid.appendChild(cell);
  });
}

function renderEfficiency(data) {
  const list     = $('#efficiencyList');
  list.innerHTML = '';
  const accounts = (data.series || []).filter((s) => s.points.length >= 2);

  if (!accounts.length) {
    list.textContent = 'Not enough readings for efficiency stats.';
    return;
  }

  accounts.forEach((s) => {
    const consumed = [];
    for (let i = 1; i < s.points.length; i++) {
      const delta = s.points[i - 1].claudeWeeklyPct - s.points[i].claudeWeeklyPct;
      if (delta > 0) consumed.push(delta);
    }
    const avg = consumed.length
      ? consumed.reduce((a, b) => a + b, 0) / consumed.length
      : null;

    const row       = document.createElement('div');
    row.className   = 'efficiency-row';
    row.innerHTML   = `
      <span class="efficiency-name">${escapeHtml(s.displayName)}</span>
      <span class="efficiency-stat">${avg != null ? `~${avg.toFixed(1)}% consumed per reading` : 'insufficient data'}</span>`;
    list.appendChild(row);
  });
}

$$('.range-btn').forEach((btn) => {
  btn.addEventListener('click', () => {
    $$('.range-btn').forEach((b) => b.classList.remove('active'));
    btn.classList.add('active');
    _currentRange = btn.dataset.range;
    _activeAccounts.clear();
    loadAnalytics();
  });
});

// ── Notifier status dot ───────────────────────────────────────────────────────

const STATUS_POLL_INTERVAL = 15_000;

async function updateNotifierStatus() {
  const dot   = $('#statusDot');
  const label = $('#statusLabel');
  if (!dot || !label) return;

  try {
    const res  = await fetch('/api/status');
    const data = await res.json();
    const conn = data.connectivity || 'offline';

    dot.className = `status-dot ${conn}`;

    let labelText = conn;
    if (conn === 'live' && data.lastTrigger) {
      labelText = `live · ${data.lastTrigger}`;
    } else if (conn === 'stale') {
      const age = data.heartbeatAgeSeconds;
      labelText  = age ? `stale · ${age}s ago` : 'stale';
    } else if (conn === 'offline') {
      labelText = 'notifier offline';
    }
    label.textContent = labelText;

    const lines = [`Notifier: ${conn}`];
    if (data.lastCaptureAt) lines.push(`Last capture: ${new Date(data.lastCaptureAt).toLocaleTimeString()}`);
    if (data.lastTrigger)   lines.push(`Trigger: ${data.lastTrigger}`);
    if (data.version)       lines.push(`v${data.version}`);
    $('#notifierStatus').title = lines.join('\n');

  } catch {
    $('#statusDot').className     = 'status-dot offline';
    $('#statusLabel').textContent  = 'dashboard offline';
  }
}

// ── Boot ──────────────────────────────────────────────────────────────────────

updateMode();            // set body.mode-* before first render
loadAccounts();
updateNotifierStatus();
setInterval(loadAccounts,         60_000);
setInterval(updateNotifierStatus, STATUS_POLL_INTERVAL);
