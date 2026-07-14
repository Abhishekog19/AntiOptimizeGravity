const CDP = require("chrome-remote-interface");

async function main() {
  const targets = await CDP.List({ port: 9222 });
  const t = targets.find(t => t.url && t.url.includes("settingsScreen"));
  if (!t) return console.log("Settings not open — open Settings first");

  const client = await CDP({ target: t.id, port: 9222 });
  await client.Runtime.enable();

  async function evaluate(expression) {
    const r = await client.Runtime.evaluate({ expression, returnByValue: true });
    return r.result?.value;
  }

  async function wait(ms) {
    return new Promise(r => setTimeout(r, ms));
  }

  // Step 1: Click "Models" nav button to make sure we're on the right tab
  console.log("Navigating to Models tab...");
  await evaluate(`
    const btns = Array.from(document.querySelectorAll('button'));
    const models = btns.find(b => b.innerText.trim() === 'Models');
    if (models) models.click();
  `);
  await wait(1000);

  // Step 2: Find and click the Refresh button (there are two - find the one near quota)
  console.log("Clicking Refresh...");
  await evaluate(`
    const btns = Array.from(document.querySelectorAll('button'));
    // Get the LAST Refresh button (the one in the Models section, not MCP section)
    const refreshBtns = btns.filter(b => b.innerText.trim() === 'Refresh');
    console.log('Found ' + refreshBtns.length + ' refresh buttons');
    if (refreshBtns.length > 0) refreshBtns[refreshBtns.length - 1].click();
  `);
  await wait(3000); // Wait for refresh to complete

  // Step 3: Read the full page text and extract quota
  console.log("Reading quota data...");
  const fullText = await evaluate(`document.documentElement.innerText`);

  // Find the quota numbers
  const lines = fullText.split('\n').map(l => l.trim()).filter(Boolean);
  
  // Find Gemini and Claude sections
  const geminiIdx = lines.findIndex(l => l.includes('Gemini Models'));
  const claudeIdx = lines.findIndex(l => l.includes('Claude and GPT'));
  
  console.log("\n=== RAW LINES AROUND QUOTA SECTION ===");
  if (geminiIdx >= 0) {
    console.log("Gemini section (lines", geminiIdx, "to", Math.min(geminiIdx+15, lines.length), "):");
    lines.slice(geminiIdx, geminiIdx + 15).forEach((l, i) => console.log(`  [${geminiIdx+i}] ${l}`));
  }
  if (claudeIdx >= 0) {
    console.log("\nClaude section (lines", claudeIdx, "to", Math.min(claudeIdx+15, lines.length), "):");
    lines.slice(claudeIdx, claudeIdx + 15).forEach((l, i) => console.log(`  [${claudeIdx+i}] ${l}`));
  }

  // Step 4: Get account email - click Account nav
  console.log("\n\nNavigating to Account tab...");
  await evaluate(`
    const btns = Array.from(document.querySelectorAll('button'));
    const acct = btns.find(b => b.innerText.trim() === 'Account');
    if (acct) acct.click();
  `);
  await wait(1000);

  const accountText = await evaluate(`document.documentElement.innerText`);
  const accountLines = accountText.split('\n').map(l => l.trim()).filter(Boolean);
  
  console.log("=== ACCOUNT PAGE (first 40 lines) ===");
  accountLines.slice(0, 40).forEach((l, i) => console.log(`  [${i}] ${l}`));

  await client.close();
}

main().catch(e => { console.error("Error:", e.message); process.exit(1); });
