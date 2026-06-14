#!/usr/bin/env node
/**
 * tv_inspect_orders.js - READ-ONLY map of the Questrade ORDERS table so we can
 * figure out how to reprice (modify) a resting stop order. Clicks the Orders tab
 * to populate it, then reads row structure + per-row buttons + any editable
 * cells. Places/modifies/cancels NOTHING.
 *
 * Usage: node tv_inspect_orders.js [--port 9225]
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
  const evalJs = async (expr, awaitP = false) => { const r = await call("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: awaitP }); return r.result && r.result.result ? r.result.result.value : undefined; };
  await new Promise(res => sock.addEventListener("open", res));

  const expr = `(async () => {
    const sleep = ms => new Promise(r=>setTimeout(r,ms));
    const vis = el => !!(el && el.offsetParent !== null && el.getClientRects().length>0);
    const tab = Array.from(document.querySelectorAll('button,[role=button],[role=tab]')).find(b=>vis(b)&&/^orders\\b/i.test((b.innerText||'').trim()));
    if (tab) { tab.click(); await sleep(700); }
    const t = document.querySelector('[data-name=\\"QUESTRADE.orders-table\\"]');
    if (!t) return JSON.stringify({found:false});
    const rows = Array.from(t.querySelectorAll('[role=row],tr')).filter(vis);
    const out = rows.slice(0,6).map(r => ({
      text: (r.innerText||'').replace(/\\s+/g,' ').slice(0,110),
      buttons: Array.from(r.querySelectorAll('button,[role=button]')).map(b=>({dn:b.getAttribute('data-name')||'', aria:b.getAttribute('aria-label')||'', txt:(b.innerText||'').slice(0,14)})).filter(b=>b.dn||b.aria||b.txt).slice(0,8),
      // cells that might be editable (the stop-price cell)
      editableCells: Array.from(r.querySelectorAll('[contenteditable=true],input,[class*=editable]')).map(c=>({tag:c.tagName, dn:c.getAttribute('data-name')||'', val:(c.value||c.innerText||'').slice(0,12)})).slice(0,6),
      // per-cell data-names (to spot a stopPrice cell we could click/edit)
      cellDataNames: Array.from(r.querySelectorAll('[data-name]')).map(e=>e.getAttribute('data-name')).filter(x=>/stop|price|cancel|modify|edit|settings/i.test(x)).filter((v,i,a)=>a.indexOf(v)===i).slice(0,12)
    }));
    return JSON.stringify({found:true, rowCount:rows.length, rows:out}, null, 1);
  })()`;
  console.log(await evalJs(expr, true) || "{}");
  sock.close();
})().catch(e => { console.error("ERR", e.message); process.exit(3); });
