#!/usr/bin/env node
/**
 * tv_inspect_positions.js - READ-ONLY. Maps the Questrade positions table so we
 * can build the cutoff "close position" exit safely (real held qty + the close
 * control per row). Also dumps the orders table (for the later stop-move work).
 * Clicks nothing, sets nothing, places nothing.
 *
 * Usage: node tv_inspect_positions.js [--port 9225]
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
  await new Promise(res => sock.addEventListener("open", res));

  const expr = `(function(){
    var vis = el => !!(el && el.offsetParent !== null && el.getClientRects().length>0);
    function tableInfo(name){
      var t = document.querySelector('[data-name=\\"'+name+'\\"]');
      if(!t) return {found:false};
      // header cells + first few data rows + any per-row buttons (close/X)
      var rows = Array.from(t.querySelectorAll('[role=row],tr')).filter(vis);
      var sample = rows.slice(0,5).map(r=>({
        text:(r.innerText||'').replace(/\\s+/g,' ').slice(0,90),
        buttons: Array.from(r.querySelectorAll('button,[role=button]')).map(b=>({dn:b.getAttribute('data-name')||'', aria:b.getAttribute('aria-label')||'', txt:(b.innerText||'').slice(0,12)})).filter(b=>b.dn||b.aria||b.txt).slice(0,6)
      }));
      // any element whose data-name/aria hints at closing a position
      var closeHints = Array.from(t.querySelectorAll('[data-name],[aria-label],[title]'))
        .map(e=>e.getAttribute('data-name')||e.getAttribute('aria-label')||e.getAttribute('title'))
        .filter(x=>x&&/close|flatten|exit|reverse/i.test(x)).filter((v,i,a)=>a.indexOf(v)===i).slice(0,10);
      return {found:true, rowCount:rows.length, sampleRows:sample, closeHints:closeHints};
    }
    return JSON.stringify({
      positions: tableInfo('QUESTRADE.positions-table'),
      orders: tableInfo('QUESTRADE.orders-table')
    }, null, 1);
  })()`;
  const res = await call("Runtime.evaluate", { expression: expr, returnByValue: true });
  console.log(res.result && res.result.result ? res.result.result.value : JSON.stringify(res.result));
  sock.close();
})().catch(e => { console.error("ERR", e.message); process.exit(3); });
