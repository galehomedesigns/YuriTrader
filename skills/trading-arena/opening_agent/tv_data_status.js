#!/usr/bin/env node
/**
 * tv_data_status.js - READ-ONLY probe: is the TradingView chart serving REAL-TIME
 * or DELAYED data? Reads the DOM/quote status only; clicks nothing, places nothing.
 *
 * Usage: node tv_data_status.js [--port 9225] [--symbol AAPL]
 *   --symbol optional: if given, switches the chart to it first (read of a US name).
 */
function arg(name, def) {
  const i = process.argv.indexOf("--" + name);
  return i === -1 ? def : process.argv[i + 1];
}
const PORT = arg("port", "9225");
const SYMBOL = arg("symbol", null);

(async () => {
  const tabs = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
  const tv = tabs.find(t => t.type === "page" && t.url && t.url.includes("tradingview.com/chart"));
  if (!tv) { console.error("no chart tab on port " + PORT); process.exit(2); }
  const ws = tv.webSocketDebuggerUrl.replace(/\/\/[^/]+\//, `//127.0.0.1:${PORT}/`);
  const sock = new WebSocket(ws);
  let id = 0; const w = {};
  sock.addEventListener("message", e => { const m = JSON.parse(e.data); if (m.id && w[m.id]) { w[m.id](m); delete w[m.id]; } });
  const call = (method, params = {}) => new Promise(res => { const i = ++id; w[i] = res; sock.send(JSON.stringify({ id: i, method, params })); });
  await new Promise(res => sock.addEventListener("open", res));
  await call("Runtime.enable");

  if (SYMBOL) {
    await call("Runtime.evaluate", { expression:
      `window.TradingViewApi && window.TradingViewApi._activeChartWidgetWV.value().setSymbol(${JSON.stringify(SYMBOL)},{})` });
    await new Promise(r => setTimeout(r, 2500));
  }

  const expr = `(function(){
    var out = {title: document.title, url: location.href};
    try {
      var w = window.TradingViewApi;
      var ch = w && w._activeChartWidgetWV && w._activeChartWidgetWV.value();
      out.activeSymbol = ch ? ch.symbol() : null;
    } catch(e){ out.activeSymbol = 'err:'+e.message; }
    // Collect any element text / title/aria that names data quality.
    var rx = /real[- ]?time|delayed|end of day|eod|data is delayed|cboe|nasdaq|nyse|by [0-9]+ ?min/i;
    var hits = [];
    var all = document.querySelectorAll('span,div,button,[title],[aria-label]');
    for (var i=0;i<all.length && hits.length<25;i++){
      var el = all[i];
      var t = (el.getAttribute('title')||'') + ' | ' + (el.getAttribute('aria-label')||'') + ' | ' + (el.innerText||'');
      t = t.replace(/\\s+/g,' ').trim();
      if (t && rx.test(t) && t.length < 90) hits.push(t);
    }
    // de-dupe
    out.statusHits = Array.from(new Set(hits));
    // explicit known TradingView data-status classes/attrs
    var ds = document.querySelector('[class*="dataMode"],[class*="quoteStatus"],[class*="marketStatus"],[class*="symbolDataMode"]');
    out.dataModeEl = ds ? (ds.innerText||ds.getAttribute('title')||ds.className).slice(0,80) : null;
    return JSON.stringify(out, null, 1);
  })()`;
  const res = await call("Runtime.evaluate", { expression: expr, returnByValue: true });
  console.log(res.result && res.result.result ? res.result.result.value : JSON.stringify(res.result));
  sock.close();
})().catch(e => { console.error("ERR", e.message); process.exit(3); });
