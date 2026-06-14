#!/usr/bin/env node
/**
 * tv_inspect_switch.js - READ-ONLY. Inspects the Stop-loss switch internals so
 * we can toggle it reliably, and dumps the orders table for a safety check.
 * Clicks nothing, sets nothing, places nothing.
 *
 * Usage: node tv_inspect_switch.js [--port 9224]
 */
function arg(name, def) { const i = process.argv.indexOf("--" + name); return i === -1 ? def : process.argv[i + 1]; }
const PORT = arg("port", "9224");

(async () => {
  const tabs = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
  const tv = tabs.find(t => t.type === "page" && t.url && t.url.includes("tradingview.com/chart"));
  if (!tv) { console.error("no chart tab"); process.exit(2); }
  const ws = tv.webSocketDebuggerUrl.replace(/\/\/[^/]+\//, `//127.0.0.1:${PORT}/`);
  const sock = new WebSocket(ws);
  let id = 0; const w = {};
  sock.addEventListener("message", e => { const m = JSON.parse(e.data); if (m.id && w[m.id]) { w[m.id](m); delete w[m.id]; } });
  const call = (method, params = {}) => new Promise(res => { const i = ++id; w[i] = res; sock.send(JSON.stringify({ id: i, method, params })); });
  await new Promise(res => sock.addEventListener("open", res));

  const expr = `(function(){
    var vis = el => !!(el && el.offsetParent !== null && el.getClientRects().length>0);
    // locate the Stop loss switch container
    var cands = Array.from(document.querySelectorAll('*')).filter(e=>vis(e)&&(e.innerText||'').trim().toLowerCase().startsWith('stop loss')&&(e.innerText||'').length<60);
    cands.sort((a,b)=>(a.innerText||'').length-(b.innerText||'').length);
    var lab=cands[0], row=lab, sw=null, h=0;
    while(row&&h<6){ sw=row.querySelector('[class*=switchContainer]'); if(sw)break; row=row.parentElement; h++; }
    var swInfo = sw ? {
      outerHTML: sw.outerHTML.replace(/\\s+/g,' ').slice(0,400),
      innerCheckbox: !!sw.querySelector('input[type=checkbox]'),
      checkboxChecked: sw.querySelector('input[type=checkbox]') ? sw.querySelector('input[type=checkbox]').checked : null,
      ariaChecked: sw.getAttribute('aria-checked'),
      role: sw.getAttribute('role')
    } : '(stop-loss switch not found)';
    // orders table (safety - any new AZI order?)
    var ot = document.querySelector('[data-name=\\"QUESTRADE.orders-table\\"]');
    var ordText = ot ? (ot.innerText||'').replace(/\\s+/g,' ').slice(0,500) : '(orders table not found)';
    return JSON.stringify({ stopLossSwitch: swInfo, ordersTable: ordText }, null, 1);
  })()`;
  const res = await call("Runtime.evaluate", { expression: expr, returnByValue: true });
  console.log(res.result && res.result.result ? res.result.result.value : JSON.stringify(res.result));
  sock.close();
})().catch(e => { console.error("ERR", e.message); process.exit(3); });
