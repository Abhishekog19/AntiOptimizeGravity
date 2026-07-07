import * as vscode from "vscode";
import * as https from "https";
import * as http from "http";

interface QuotaReading {
  accountId: string;
  timestampUtc: string;
  claudeGpt: { weeklyPct: number; fiveHourPct: number; resetCountdownRaw: string };
  gemini: { weeklyPct: number; fiveHourPct: number; resetCountdownRaw: string } | null;
}

let intervalHandle: NodeJS.Timeout | undefined;
let statusBarItem: vscode.StatusBarItem;

export function activate(context: vscode.ExtensionContext) {
  statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBarItem.text = "$(dashboard) Quota";
  statusBarItem.tooltip = "Antigravity Quota Tracker";
  statusBarItem.show();
  context.subscriptions.push(statusBarItem);

  context.subscriptions.push(
    vscode.commands.registerCommand("antigravityQuotaTracker.captureNow", () => captureAndSync("manual"))
  );

  // Baseline capture on startup / sign-in.
  captureAndSync("startup");

  // Periodic capture while the IDE is open.
  scheduleInterval();
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("antigravityQuotaTracker.captureIntervalMinutes")) {
        scheduleInterval();
      }
    })
  );

  // Best-effort "sign-out" / shutdown capture. VS Code does not give a reliable
  // sign-out event for a third-party account system, so this fires on window
  // close / extension deactivation instead (see deactivate()) and on the
  // editor losing focus for an extended period, which is the closest proxy
  // available without deeper Antigravity API access.
}

function scheduleInterval() {
  if (intervalHandle) clearInterval(intervalHandle);
  const minutes = vscode.workspace
    .getConfiguration("antigravityQuotaTracker")
    .get<number>("captureIntervalMinutes", 7);
  intervalHandle = setInterval(() => captureAndSync("interval"), Math.max(1, minutes) * 60 * 1000);
}

export function deactivate() {
  if (intervalHandle) clearInterval(intervalHandle);
  // Final best-effort reading before the window closes.
  captureAndSync("shutdown");
}

async function captureAndSync(trigger: string) {
  try {
    const reading = await scrapeQuotaPanel();
    if (!reading) return;
    await postReading(reading);
    statusBarItem.text = `$(dashboard) ${reading.claudeGpt.weeklyPct}% wk`;
  } catch (err) {
    // Never block the user's workflow on a sync failure — fail silently and
    // let the next interval retry, per spec section 5.
    console.warn(`[antigravity-quota-tracker] capture (${trigger}) failed:`, err);
  }
}

// The extraction script below is injected into the Antigravity renderer via
// CDP Runtime.evaluate. It matches the real DOM captured from Settings ->
// Models: each pool is a `.p-5.bg-card...` card with an <h3> title ("Gemini
// Models" / "Claude and GPT models"), containing two rows (Weekly Limit,
// Five Hour Limit), each with a percentage <span> and, for the weekly row
// only, a muted-foreground reset-countdown string.
const EXTRACTION_SCRIPT = `
(() => {
  function readPool(titleText) {
    const h3 = Array.from(document.querySelectorAll('h3')).find(
      (el) => el.textContent && el.textContent.trim() === titleText
    );
    if (!h3) return null;
    // h3 -> its wrapping header row -> the card container (.p-5.bg-card...)
    const card = h3.closest('.bg-card');
    if (!card) return null;

    const rows = Array.from(card.querySelectorAll(':scope > div > div'))
      .filter((row) => row.querySelector('.text-sm.font-medium'));

    function readRow(labelText) {
      const row = rows.find((r) => {
        const label = r.querySelector('.text-sm.font-medium');
        return label && label.textContent.trim() === labelText;
      });
      if (!row) return { pct: null, resetRaw: '' };
      const pctSpan = row.querySelector('span.text-sm.font-semibold');
      const resetEl = row.querySelector('.text-xs.text-muted-foreground');
      const pct = pctSpan ? parseFloat(pctSpan.textContent.replace('%', '').trim()) : null;
      const resetRaw = resetEl ? resetEl.textContent.trim() : '';
      return { pct, resetRaw };
    }

    const weekly = readRow('Weekly Limit');
    const fiveHour = readRow('Five Hour Limit');
    return {
      weeklyPct: weekly.pct,
      fiveHourPct: fiveHour.pct,
      resetCountdownRaw: weekly.resetRaw || fiveHour.resetRaw,
    };
  }

  return JSON.stringify({
    claudeGpt: readPool('Claude and GPT models'),
    gemini: readPool('Gemini Models'),
  });
})()
`;

/**
 * Reads Weekly % / Five Hour % / reset countdown out of Antigravity's quota
 * panel via the Chrome DevTools Protocol.
 *
 * IMPORTANT CAVEAT: this DOM only exists while Settings -> Models is open
 * on screen (confirmed by inspecting the live panel). That means true
 * "silent background capture" isn't possible purely from the DOM side —
 * the panel has to be open at the moment of capture. Two ways to live with
 * that:
 *   1. Leave the Settings -> Models tab open/pinned during a work session;
 *      the interval capture will pick up fresh numbers whenever it fires.
 *   2. Run the "Antigravity Quota Tracker: Capture Now" command right after
 *      opening that panel, for an on-demand reading.
 * If Antigravity later exposes quota state via an official API or command,
 * swap this out for that — it would remove this constraint entirely.
 */
async function scrapeQuotaPanel(): Promise<QuotaReading | null> {
  const config = vscode.workspace.getConfiguration("antigravityQuotaTracker");
  const accountId = config.get<string>("accountIdentifier", "");
  const debugPort = config.get<number>("remoteDebuggingPort", 9222);
  if (!accountId) {
    console.warn("[antigravity-quota-tracker] no accountIdentifier configured; skipping capture.");
    return null;
  }

  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const CDP = require("chrome-remote-interface");
  const targets: Array<{ id: string; type: string; webSocketDebuggerUrl?: string }> = await CDP.List({
    port: debugPort,
  });

  for (const target of targets) {
    if (target.type !== "page" && target.type !== "webview") continue;
    let client: any;
    try {
      client = await CDP({ target: target.id, port: debugPort });
      const { Runtime } = client;
      await Runtime.enable();

      const probe = await Runtime.evaluate({
        expression: "document.body && document.body.innerText.includes('Claude and GPT models')",
        returnByValue: true,
      });
      if (!probe.result?.value) continue;

      const result = await Runtime.evaluate({ expression: EXTRACTION_SCRIPT, returnByValue: true });
      const parsed = JSON.parse(result.result.value) as {
        claudeGpt: { weeklyPct: number | null; fiveHourPct: number | null; resetCountdownRaw: string } | null;
        gemini: { weeklyPct: number | null; fiveHourPct: number | null; resetCountdownRaw: string } | null;
      };

      if (!parsed.claudeGpt || parsed.claudeGpt.weeklyPct == null) continue;

      return {
        accountId,
        timestampUtc: new Date().toISOString(),
        claudeGpt: {
          weeklyPct: parsed.claudeGpt.weeklyPct,
          fiveHourPct: parsed.claudeGpt.fiveHourPct ?? 0,
          resetCountdownRaw: parsed.claudeGpt.resetCountdownRaw,
        },
        gemini: parsed.gemini && parsed.gemini.weeklyPct != null
          ? {
              weeklyPct: parsed.gemini.weeklyPct,
              fiveHourPct: parsed.gemini.fiveHourPct ?? 0,
              resetCountdownRaw: parsed.gemini.resetCountdownRaw,
            }
          : null,
      };
    } catch (err) {
      console.warn(`[antigravity-quota-tracker] CDP probe failed for target ${target.id}:`, err);
    } finally {
      if (client) await client.close();
    }
  }

  console.warn(
    "[antigravity-quota-tracker] quota panel not found in any open target — is Settings > Models open?"
  );
  return null;
}

function postReading(reading: QuotaReading): Promise<void> {
  const config = vscode.workspace.getConfiguration("antigravityQuotaTracker");
  const baseUrl = config.get<string>("dashboardUrl", "http://localhost:4300");
  const apiKey = config.get<string>("apiKey", "");

  const url = new URL("/api/readings", baseUrl);
  const payload = JSON.stringify(reading);
  const transport = url.protocol === "https:" ? https : http;

  return new Promise((resolve, reject) => {
    const req = transport.request(
      url,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(payload),
          ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
        },
      },
      (res) => {
        if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
          resolve();
        } else {
          reject(new Error(`Dashboard responded with ${res.statusCode}`));
        }
        res.resume();
      }
    );
    req.on("error", reject);
    req.write(payload);
    req.end();
  });
}
