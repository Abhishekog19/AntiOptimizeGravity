const CDP = require("chrome-remote-interface");

async function main() {
  const targets = await CDP.List({ port: 9222 });
  
  // Look for EITHER Models or Account settings screen
  const t = targets.find(t => t.url && t.url.includes("settingsScreen"));
  if (!t) return console.log("Settings not open");

  const client = await CDP({ target: t.id, port: 9222 });
  await client.Runtime.enable();

  async function evaluate(expression) {
    const r = await client.Runtime.evaluate({ expression, returnByValue: true });
    return r.result?.value;
  }

  async function wait(ms) { return new Promise(r => setTimeout(r, ms)); }

  // The Account page is already open from your screenshot
  // Try reading email directly from the DOM structure
  console.log("Reading email from DOM...");
  
  const emailFromDOM = await evaluate(`
    // Look for the email text near "Email" label
    const allElements = Array.from(document.querySelectorAll('*'));
    let emailEl = null;
    for (const el of allElements) {
      if (el.children.length === 0 && el.innerText && 
          el.innerText.includes('@') && el.innerText.includes('.')) {
        emailEl = el;
      }
    }
    emailEl ? emailEl.innerText.trim() : 'not found';
  `);
  console.log("Email from DOM leaf nodes:", emailFromDOM);

  // Also get full account page text now that it's open
  const fullText = await evaluate(`document.documentElement.innerText`);
  const lines = fullText.split('\n').map(l => l.trim()).filter(Boolean);
  
  // Find "Email" label and get the next line
  const emailLabelIdx = lines.findIndex(l => l === 'Email');
  console.log("\nLines around 'Email' label:");
  if (emailLabelIdx >= 0) {
    lines.slice(emailLabelIdx, emailLabelIdx + 5).forEach((l, i) => 
      console.log(`  [${emailLabelIdx + i}] ${l}`)
    );
  } else {
    console.log("'Email' label not found in page text");
    // Show first 30 lines to see what's there
    console.log("First 30 lines of current page:");
    lines.slice(0, 30).forEach((l, i) => console.log(`  [${i}] ${l}`));
  }

  // Now navigate back to Models and do a full capture
  console.log("\n\nNavigating back to Models and doing full capture...");
  await evaluate(`
    const btns = Array.from(document.querySelectorAll('button'));
    const models = btns.find(b => b.innerText.trim() === 'Models');
    if (models) models.click();
  `);
  await wait(800);

  // Click refresh
  await evaluate(`
    const btns = Array.from(document.querySelectorAll('button'));
    const refreshBtns = btns.filter(b => b.innerText.trim() === 'Refresh');
    if (refreshBtns.length > 0) refreshBtns[refreshBtns.length - 1].click();
  `);
  await wait(3000);

  // Parse quota
  const quotaText = await evaluate(`document.documentElement.innerText`);
  const qlines = quotaText.split('\n').map(l => l.trim()).filter(Boolean);
  
  function parseSection(lines, startIdx) {
    const result = {};
    let i = startIdx + 1;
    while (i < lines.length && i < startIdx + 10) {
      if (lines[i] === 'Weekly Limit') {
        // Next non-reset line with % is the value
        for (let j = i+1; j < i+4; j++) {
          if (lines[j] && lines[j].match(/^\d+%$/)) {
            result.weeklyPct = parseInt(lines[j]);
            break;
          }
          if (lines[j] && lines[j].match(/refresh in/)) {
            result.weeklyReset = lines[j];
          }
        }
      }
      if (lines[i] === 'Five Hour Limit') {
        for (let j = i+1; j < i+4; j++) {
          if (lines[j] && lines[j].match(/^\d+%$/)) {
            result.fiveHourPct = parseInt(lines[j]);
            break;
          }
          if (lines[j] && lines[j].match(/refresh in/)) {
            result.fiveHourReset = lines[j];
          }
        }
      }
      i++;
    }
    return result;
  }

  const geminiIdx = qlines.findIndex(l => l.includes('Gemini Models'));
  const claudeIdx = qlines.findIndex(l => l.includes('Claude and GPT'));

  const gemini = geminiIdx >= 0 ? parseSection(qlines, geminiIdx) : null;
  const claude = claudeIdx >= 0 ? parseSection(qlines, claudeIdx) : null;

  console.log("\n=== PARSED QUOTA DATA ===");
  console.log("Gemini:", JSON.stringify(gemini, null, 2));
  console.log("Claude/GPT:", JSON.stringify(claude, null, 2));

  await client.close();
}

main().catch(e => { console.error("Error:", e.message); process.exit(1); });
