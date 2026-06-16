#!/usr/bin/env node
/**
 * tv_export_bars.js - pull the REAL-TIME 2-min OHLC series for a symbol off the
 * TradingView chart via CDP (the upgraded TV account's live feed). Replaces IBKR
 * reqHistoricalData as the bar source for the opening classifier.
 *
 * exportData() is disabled by TradingView ("not supported"), so we read the loaded
 * series straight from the chart model: getSeries()._series.bars() -> PlotList of
 * [time, open, high, low, close, volume]. Verified to yield 300 bars on AAPL.
 *
 * Switches the chart to --symbol at --res, waits for >= --min bars to load, then
 * prints JSON {symbol, resolution, count, bars:[{time,open,high,low,close,volume}]}
 * oldest->newest. Exit 0 if count>=min, 4 if it couldn't load enough, 2/3 on CDP err.
 *
 * Usage: node tv_export_bars.js --symbol AAPL [--port 9225] [--res 2] [--min 200]
 *   --no-switch   read whatever the chart currently shows (don't change symbol)
 */
function arg(name, def) { const i = process.argv.indexOf("--" + name); return i === -1 ? def : process.argv[i + 1]; }
const has = (name) => process.argv.includes("--" + name);
const PORT = arg("port", "9225");
const SYMBOL = arg("symbol", null);
const RES = arg("res", "2");
const MIN = parseInt(arg("min", "200"), 10);

(async () => {
  const tabs = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
  const tv = tabs.find(t => t.type === "page" && t.url && t.url.includes("tradingview.com/chart"));
  if (!tv) { console.error("no chart tab on port " + PORT); process.exit(2); }
  const ws = tv.webSocketDebuggerUrl.replace(/\/\/[^/]+\//, `//127.0.0.1:${PORT}/`);
  const sock = new WebSocket(ws); let id = 0; const w = {};
  sock.addEventListener("message", e => { const m = JSON.parse(e.data); if (m.id && w[m.id]) { w[m.id](m); delete w[m.id]; } });
  const call = (method, params = {}) => new Promise(r => { const i = ++id; w[i] = r; sock.send(JSON.stringify({ id: i, method, params })); });
  const evalJs = (expression, awaitPromise = false) =>
    call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise }).then(r => r.result && r.result.result ? r.result.result.value : null);
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  await new Promise(r => sock.addEventListener("open", r));
  await call("Runtime.enable");

  if (SYMBOL && !has("no-switch")) {
    await evalJs(`(function(){var c=window.TradingViewApi._activeChartWidgetWV.value();
      c.setSymbol(${JSON.stringify(SYMBOL)},{}); try{c.setResolution(${JSON.stringify(RES)},function(){});}catch(e){} return 1;})()`);
  } else if (!has("no-switch")) {
    await evalJs(`(function(){try{window.TradingViewApi._activeChartWidgetWV.value().setResolution(${JSON.stringify(RES)},function(){});}catch(e){} return 1;})()`);
  }

  // Wait until the NEW symbol's data has actually rendered — not the previous
  // symbol's still-loaded bars. The chart title only shows "TICKER <price>" once
  // the new series has loaded, so that (+ not loading, + >=MIN bars) is the gate.
  const reqTicker = SYMBOL ? SYMBOL.split(":").pop().toUpperCase() : null;
  const readyExpr = `(function(){try{
    var ch=window.TradingViewApi._activeChartWidgetWV.value(); var s=ch.getSeries();
    var sym=''; try{sym=ch.symbol();}catch(e){}
    var loading=true; try{loading=s._series.isLoading();}catch(e){}
    var bc=-1; try{bc=s.barsCount();}catch(e){}
    return JSON.stringify({sym:sym, loading:loading, bc:bc, title:document.title});
  }catch(e){return JSON.stringify({err:String(e.message)});}})()`;
  let count = -1;
  for (let i = 0; i < 30; i++) {            // ~15s max
    const st = JSON.parse((await evalJs(readyExpr)) || "{}");
    count = st.bc;
    const titleReady = !reqTicker ||
      new RegExp("^" + reqTicker.replace(/[.^$*+?()[\]{}|\\]/g, "\\$&") + "\\s+[0-9]").test((st.title || "").toUpperCase());
    const symOk = !reqTicker || (st.sym || "").toUpperCase().endsWith(reqTicker);
    if (symOk && titleReady && st.loading === false && count >= MIN) break;
    await sleep(500);
  }

  const extract = `(function(){
    var out={};
    var ch=window.TradingViewApi._activeChartWidgetWV.value();
    var s=ch.getSeries();
    out.symbol=s.symbolSource?(function(){try{return ch.symbol();}catch(e){return null;}})():null;
    out.resolution=(function(){try{return ch.resolution();}catch(e){return null;}})();
    var pl=s._series.bars();           // PlotList of [time, o, h, l, c, v]
    var bars=[];
    pl.each(function(i,item){ var v=item && item.value ? item.value : item;
      bars.push({time:v[0], open:v[1], high:v[2], low:v[3], close:v[4], volume:v[5]||0}); return false; });
    out.count=bars.length; out.bars=bars;
    return JSON.stringify(out);
  })()`;
  const json = await evalJs(extract);
  sock.close();
  if (!json) { console.error("extraction returned nothing"); process.exit(3); }
  console.log(json);
  const parsed = JSON.parse(json);
  process.exit(parsed.count >= MIN ? 0 : 4);
})().catch(e => { console.error("ERR", e.message); process.exit(3); });
