const CDP = require("chrome-remote-interface");

async function main() {
  const targets = await CDP.List({ port: 9222 });
  const t = targets.find(t => t.url && t.url.includes("settingsScreen"));
  if (!t) return console.log("Settings not open");

  const client = await CDP({ target: t.id, port: 9222 });
  await client.Runtime.enable();

  async function evaluate(expression) {
    const r = await client.Runtime.evaluate({ expression, returnByValue: true });
    return r.result?.value;
  }

  // Try navigating to Account screen via URL change
  console.log("Trying URL navigation to Account screen...");
  await evaluate(`history.pushState({}, '', '/?settingsScreen=Account')`);
  await new Promise(r => setTimeout(r, 1500));

  const text1 = await evaluate(`document.documentElement.innerText`);
  const lines1 = text1.split('\n').map(l => l.trim()).filter(Boolean);
  console.log("After URL change (first 50 lines):");
  lines1.slice(0, 50).forEach((l, i) => console.log(`  [${i}] ${l}`));

  // Also look for email-like patterns anywhere in the full page text
  console.log("\n\nSearching for email patterns in full page...");
  const emailMatches = await evaluate(`
    const text = document.documentElement.innerText;
    const emailRegex = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g;
    const matches = text.match(emailRegex) || [];
    JSON.stringify([...new Set(matches)]);
  `);
  console.log("Email addresses found:", emailMatches);

  // Also check for any data attributes or hidden text with account info
  console.log("\nChecking for account info in DOM attributes...");
  const domSearch = await evaluate(`
    JSON.stringify(
      Array.from(document.querySelectorAll('[data-account], [data-email], [data-user]'))
        .map(el => ({tag: el.tagName, attrs: el.getAttributeNames().reduce((a,k) => ({...a,[k]:el.getAttribute(k)}),{})}))
    )
  `);
  console.log("DOM account attributes:", domSearch);

  // Check window/global variables for account info
  console.log("\nChecking global JS variables...");
  const globals = await evaluate(`
    JSON.stringify({
      hasAntigravity: typeof antigravity !== 'undefined',
      hasAccount: typeof account !== 'undefined', 
      hasUser: typeof user !== 'undefined',
      hasCurrentUser: typeof currentUser !== 'undefined',
      windowKeys: Object.keys(window).filter(k => 
        k.toLowerCase().includes('account') || 
        k.toLowerCase().includes('user') || 
        k.toLowerCase().includes('auth') ||
        k.toLowerCase().includes('email')
      )
    })
  `);
  console.log("Global vars:", globals);

  await client.close();
}

main().catch(e => { console.error("Error:", e.message); process.exit(1); });
