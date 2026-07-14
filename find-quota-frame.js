/**
 * find-quota-frame.js v2
 * Uses Runtime.executionContexts (existing contexts) instead of waiting
 * for executionContextCreated events which fire before we connect.
 */
const CDP = require("chrome-remote-interface");
const PORT = 9222;
const SEARCH_TEXT = "Claude and GPT models";

async function main() {
  const targets = await CDP.List({ port: PORT });
  const candidates = targets.filter(t => t.type === "page" || t.type === "webview" || t.type === "iframe");

  console.log(`Found ${candidates.length} target(s):`);
  candidates.forEach((t, i) => console.log(`  [${i}] ${t.type} | "${t.title}" | ${t.url}`));
  console.log("");

  for (const target of candidates) {
    let client;
    try {
      client = await CDP({ target: target.id, port: PORT });
      const { Runtime } = client;
      await Runtime.enable();

      // Give the runtime a moment to stabilize
      await new Promise(r => setTimeout(r, 500));

      // Fetch all existing execution contexts
      const { contexts } = await Runtime.getExecutionContexts ? 
        Runtime.getExecutionContexts() :
        { contexts: [] };

      // Fallback: use the default context (id=1) if getExecutionContexts not available
      const contextIds = contexts.length > 0
        ? contexts.map(c => ({ id: c.uniqueId || c.id, name: c.name, origin: c.origin }))
        : [{ id: undefined, name: "default", origin: "default" }];

      console.log(`Target "${target.title}" — ${contextIds.length} context(s):`);

      for (const ctx of contextIds) {
        try {
          const evalOpts = {
            expression: `(function() {
              try {
                var text = document.documentElement.innerText || document.body.innerText || '';
                return JSON.stringify({
                  found: text.includes(${JSON.stringify(SEARCH_TEXT)}),
                  title: document.title,
                  url: location.href,
                  snippet: text.indexOf(${JSON.stringify(SEARCH_TEXT)}) >= 0
                    ? text.slice(Math.max(0, text.indexOf(${JSON.stringify(SEARCH_TEXT)}) - 30),
                               text.indexOf(${JSON.stringify(SEARCH_TEXT)}) + 400)
                    : null
                });
              } catch(e) { return JSON.stringify({error: e.message}); }
            })()`,
            returnByValue: true,
            ...(ctx.id !== undefined ? { uniqueContextId: ctx.id } : {})
          };

          const result = await Runtime.evaluate(evalOpts);
          const val = JSON.parse(result.result?.value || '{}');

          console.log(`  ctx="${ctx.name || ctx.id}"  origin=${ctx.origin}  found=${val.found}  url=${val.url}`);
          if (val.found) {
            console.log("\n  *** QUOTA PANEL FOUND ***");
            console.log("  Snippet:\n", val.snippet);
            console.log("");
          }
          if (val.error) console.log(`  ERROR: ${val.error}`);
        } catch (e) {
          console.log(`  ctx="${ctx.name}"  => failed: ${e.message}`);
        }
      }

      // Also try the default context directly (no contextId specified)
      console.log("  [trying default context...]");
      try {
        const def = await Runtime.evaluate({
          expression: `JSON.stringify({
            found: document.documentElement.innerText.includes(${JSON.stringify(SEARCH_TEXT)}),
            iframeCount: document.querySelectorAll('iframe').length,
            title: document.title,
            bodySnippet: document.documentElement.innerText.slice(0, 200)
          })`,
          returnByValue: true
        });
        const v = JSON.parse(def.result?.value || '{}');
        console.log(`  default ctx: found=${v.found}  iframes=${v.iframeCount}  title=${v.title}`);
        console.log(`  body snippet: ${v.bodySnippet}`);
        if (v.found) {
          console.log("\n  *** FOUND IN DEFAULT CONTEXT ***");
          const full = await Runtime.evaluate({
            expression: `document.documentElement.innerText.slice(
              Math.max(0, document.documentElement.innerText.indexOf(${JSON.stringify(SEARCH_TEXT)}) - 30),
              document.documentElement.innerText.indexOf(${JSON.stringify(SEARCH_TEXT)}) + 500
            )`,
            returnByValue: true
          });
          console.log(full.result?.value);
        }
      } catch(e) {
        console.log(`  default ctx failed: ${e.message}`);
      }
      console.log("");

    } catch (err) {
      console.log(`Target "${target.title}" failed: ${err.message}\n`);
    } finally {
      if (client) await client.close();
    }
  }
  console.log("Done.");
}

main().catch(e => { console.error("Fatal:", e); process.exit(1); });