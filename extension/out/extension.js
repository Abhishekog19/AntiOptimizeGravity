"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = require("vscode");
const https = require("https");
const http = require("http");
let intervalHandle;
let statusBarItem;
function activate(context) {
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBarItem.text = "$(dashboard) Quota";
    statusBarItem.tooltip = "Antigravity Quota Tracker";
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);
    context.subscriptions.push(vscode.commands.registerCommand("antigravityQuotaTracker.captureNow", () => captureAndSync("manual")));
    // Baseline capture on startup / sign-in.
    captureAndSync("startup");
    // Periodic capture while the IDE is open.
    scheduleInterval();
    context.subscriptions.push(vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration("antigravityQuotaTracker.captureIntervalMinutes")) {
            scheduleInterval();
        }
    }));
    // Best-effort "sign-out" / shutdown capture. VS Code does not give a reliable
    // sign-out event for a third-party account system, so this fires on window
    // close / extension deactivation instead (see deactivate()) and on the
    // editor losing focus for an extended period, which is the closest proxy
    // available without deeper Antigravity API access.
}
function scheduleInterval() {
    if (intervalHandle)
        clearInterval(intervalHandle);
    const minutes = vscode.workspace
        .getConfiguration("antigravityQuotaTracker")
        .get("captureIntervalMinutes", 7);
    intervalHandle = setInterval(() => captureAndSync("interval"), Math.max(1, minutes) * 60 * 1000);
}
function deactivate() {
    if (intervalHandle)
        clearInterval(intervalHandle);
    // Final best-effort reading before the window closes.
    captureAndSync("shutdown");
}
async function captureAndSync(trigger) {
    try {
        const reading = await scrapeQuotaPanel();
        if (!reading)
            return;
        await postReading(reading);
        statusBarItem.text = `$(dashboard) ${reading.claudeGpt.weeklyPct}% wk`;
    }
    catch (err) {
        // Never block the user's workflow on a sync failure — fail silently and
        // let the next interval retry, per spec section 5.
        console.warn(`[antigravity-quota-tracker] capture (${trigger}) failed:`, err);
    }
}
/**
 * Reads the Weekly % / Five Hour % / reset countdown out of Antigravity's
 * quota panel.
 *
 * OPEN ITEM (see spec section 8): a standard VS Code extension cannot read
 * the DOM of another extension's webview through the public API — there is
 * no supported `document.querySelector` reach-through. In practice this
 * needs one of:
 *   1. An official Antigravity API/command that exposes quota state
 *      (check `vscode.extensions.getExtension('google.antigravity')?.exports`
 *      if/when Antigravity ships a programmatic surface), or
 *   2. Attaching to the running window via the Chrome DevTools Protocol
 *      (VS Code can be launched with --remote-debugging-port) and querying
 *      the webview's iframe document directly. This is fragile and will
 *      break across Antigravity UI updates.
 *
 * This stub returns null until one of those is wired up. Swap in the real
 * implementation once you've inspected the live panel's DOM structure.
 */
async function scrapeQuotaPanel() {
    const config = vscode.workspace.getConfiguration("antigravityQuotaTracker");
    const accountId = config.get("accountIdentifier", "");
    if (!accountId) {
        console.warn("[antigravity-quota-tracker] no accountIdentifier configured; skipping capture.");
        return null;
    }
    // --- Replace this block with real DOM/CDP-derived values. ---
    const notYetImplemented = true;
    if (notYetImplemented) {
        return null;
    }
    // --------------------------------------------------------------
    return {
        accountId,
        timestampUtc: new Date().toISOString(),
        claudeGpt: { weeklyPct: 0, fiveHourPct: 0, resetCountdownRaw: "" },
        gemini: { weeklyPct: 0, fiveHourPct: 0, resetCountdownRaw: "" },
    };
}
function postReading(reading) {
    const config = vscode.workspace.getConfiguration("antigravityQuotaTracker");
    const baseUrl = config.get("dashboardUrl", "http://localhost:4300");
    const apiKey = config.get("apiKey", "");
    const url = new URL("/api/readings", baseUrl);
    const payload = JSON.stringify(reading);
    const transport = url.protocol === "https:" ? https : http;
    return new Promise((resolve, reject) => {
        const req = transport.request(url, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Content-Length": Buffer.byteLength(payload),
                ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
            },
        }, (res) => {
            if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
                resolve();
            }
            else {
                reject(new Error(`Dashboard responded with ${res.statusCode}`));
            }
            res.resume();
        });
        req.on("error", reject);
        req.write(payload);
        req.end();
    });
}
//# sourceMappingURL=extension.js.map