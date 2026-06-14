#!/usr/bin/env node
/**
 * tv_positions.js - READ the Questrade positions table and print it as JSON.
 * Clicks the Positions tab (so the table is populated) then reads it; does NOT
 * place/modify/close anything. Used by advisory_monitor to cross-check before
 * staging a cutoff close (never sell what you don't hold).
 *
 * Usage: node tv_positions.js [--port 9225]
 * Output: JSON array [{symbol, side, qty}]
 */
function arg(name, def) { const i = process.argv.indexOf("--" + name); return i === -1 ? def : process.argv[i + 1]; }
const PORT = arg("port", "9225");

(async () => {
  const tabs = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
  const tv = tabs.find(t => t.type === "page" && t.url && t.url.includes("tradingview.com/chart"));
  if (!tv) { console.error("no chart tab"); process.exit(2); }
  const ws = tv.webSocketDebuggerUrl.replace(/\/\/[^/]+\//, `//127.0.0.1:${PORT}/`);
  const sock = new WebSocket(ws);
  let id = 0; const w = {};
  sock.addEventListener("message", e => { const m = JSON.parse(e.data); if (m.id && w[m.id]) { w[m.id](m); delete w[m.id]; } });
  const call = (method, params = {}) => new Promise(res => { const i = ++id; w[i] = res; sock.send(JSON.stringify({ id: i, method, params })); });
  const evalJs = async (expr, awaitP = false) => {
    const r = await call("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: awaitP });
    return r.result && r.result.result ? r.result.result.value : undefined;
  };
  await new Promise(res => sock.addEventListener("open", res));

  const expr = `(async () => {
    const sleep = ms => new Promise(r=>setTimeout(r,ms));
    const vis = el => !!(el && el.offsetParent !== null && el.getClientRects().length>0);
    // make sure the Positions tab is active so the table is populated
    const tab = Array.from(document.querySelectorAll('button,[role=button],[role=tab]')).find(b=>vis(b)&&/^positions\\b/i.test((b.innerText||'').trim()));
    if (tab) { tab.click(); await sleep(800); }
    const t = document.querySelector('[data-name=\\"QUESTRADE.positions-table\\"]');
    if (!t) return JSON.stringify([]);
    const rows = Array.from(t.querySelectorAll('[role=row],tr')).filter(vis);
    const out = [];
    for (const r of rows) {
      const toks = (r.innerText||'').replace(/\\s+/g,' ').trim().split(' ');
      // row shape: SYMBOL  Long|Short  QTY  ...
      if (toks.length < 3) continue;
      const side = toks[1];
      if (!/^(long|short)$/i.test(side)) continue;     // skips the header row
      const qty = parseFloat(String(toks[2]).replace(/,/g,''));
      if (!(qty > 0)) continue;
      out.push({ symbol: toks[0], side: side.toLowerCase(), qty });
    }
    return JSON.stringify(out);
  })()`;
  const val = await evalJs(expr, true);
  console.log(val || "[]");
  sock.close();
})().catch(e => { console.error("ERR", e.message); process.exit(3); });
