const CDP = require("chrome-remote-interface");

async function main() {
  const targets = await CDP.List({ port: 9222 });
  const t = targets.find(t => t.url && t.url.includes("settingsScreen"));
  if (!t) return console.log("Settings page not open — open Settings -> Models first");

  const client = await CDP({ target: t.id, port: 9222 });
  await client.Runtime.enable();

  // Test B1: List all buttons
  const buttons = await client.Runtime.evaluate({
    expression: `Array.from(document.querySelectorAll('button')).map(b => b.innerText.trim()).filter(Boolean)`,
    returnByValue: true
  });
  console.log("Buttons found:", JSON.stringify(buttons.result.value, null, 2));

  // Test B2: Click the Refresh button
  const clickResult = await client.Runtime.evaluate({
    expression: `
      const btn = Array.from(document.querySelectorAll('button'))
        .find(b => b.innerText.trim() === 'Refresh');
      if (btn) { btn.click(); 'clicked'; } else { 'not found'; }
    `,
    returnByValue: true
  });
  console.log("Refresh button click result:", clickResult.result.value);

  // Wait 3 seconds for data to refresh
  await new Promise(r => setTimeout(r, 3000));

  // Test B3: Read quota data after refresh
  const quota = await client.Runtime.evaluate({
    expression: `document.documentElement.innerText`,
    returnByValue: true
  });

  // Extract just the quota section
  const text = quota.result.value;
  const start = text.indexOf("Model Quota");
  console.log("\nQuota section after refresh:\n", start >= 0 ? text.slice(start, start + 600) : "Not found");

  // Test A: Try to find account email
  const accountSection = text.indexOf("Account");
  console.log("\nAccount section:\n", accountSection >= 0 ? text.slice(accountSection, accountSection + 300) : "Not found");

  await client.close();
}

main().catch(e => { console.error("Error:", e.message); process.exit(1); });
