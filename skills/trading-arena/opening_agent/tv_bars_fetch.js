#!/usr/bin/env node
/**
 * tv_bars_fetch.js - production bar feed: pull real-time 2-min OHLC bars for a
 * LIST of symbols off a DEDICATED background TradingView data tab (isolated from
 * the trading/order chart). Replaces IBKR reqHistoricalData.
 *
 * The data tab is created once and reused (its targetId is persisted via tv_tab.js
 * so order tools never stage onto it). Switching the data tab's symbol does NOT
 * touch the foreground trading chart.
 *
 * Usage: node tv_bars_fetch.js --symbols NASDAQ:AAPL,NYSE:F [--min 200] [--res 2] [--port 9225]
 * Output (stdout): {"results":[{"symbol":"NASDAQ:AAPL","count":300,"bars":[{time,open,high,low,close,volume}...]}|{"symbol":...,"error":"..."}]}
 */
const tab = require("./tv_tab");
const crypto = require("crypto");
function arg(name, def) { const i = process.argv.indexOf("--" + name); return i === -1 ? def : process.argv[i + 1]; }
const PORT = arg("port", "9225");
const SYMBOLS = (arg("symbols", "") || "").split(",").map(s => s.trim()).filter(Boolean);
const MIN = parseInt(arg("min", "200"), 10);
const RES = arg("res", "2");
const sleep = ms => new Promise(r => setTimeout(r, ms));

function mkConn(wsUrl) {
  const sock = new WebSocket(wsUrl); let id = 0; const w = {};
  sock.addEventListener("message", e => { const m = JSON.parse(e.data); if (m.id && w[m.id]) { w[m.id](m); delete w[m.id]; } });
  const call = (method, params = {}) => new Promise(r => { const i = ++id; w[i] = r; sock.send(JSON.stringify({ id: i, method, params })); });
  const ready = new Promise(r => sock.addEventListener("open", r));
  return { call, ready, sock };
}

async function jsonGet(path) { return (await (await fetch(`http://127.0.0.1:${PORT}${path}`)).json()); }

// Flush stdout BEFORE exiting — process.exit() truncates large unflushed pipe
// writes (the JSON for many symbols is >64KB), which silently corrupts the result.
function emit(obj, code = 0) { process.stdout.write(JSON.stringify(obj) + "\n", () => process.exit(code)); }

const MARK = "__OPENING_DATA_TAB__";
const NONCE = "__OPENING_DATA_NONCE__";

async function connect(page) {
  const ws = page.webSocketDebuggerUrl.replace(/\/\/[^/]+\//, `//127.0.0.1:${PORT}/`);
  const P = mkConn(ws); await P.ready; await P.call("Runtime.enable");
  return P;
}

async function readNonce(P) {
  // read the nonce we stamped into the data tab's window (null if absent)
  try {
    const r = await P.call("Runtime.evaluate", { expression: `String(window.${NONCE}||"")`, returnByValue: true });
    return (r.result && r.result.result && r.result.result.value) || null;
  } catch (e) { return null; }
}

async function waitApi(P) {
  for (let i = 0; i < 30; i++) {
    const r = await P.call("Runtime.evaluate", { expression: "!!(window.TradingViewApi && window.TradingViewApi._activeChartWidgetWV && window.TradingViewApi._activeChartWidgetWV.value())", returnByValue: true });
    if (r.result && r.result.result && r.result.result.value) return true;
    await sleep(600);
  }
  throw new Error("TradingViewApi not ready in data tab");
}

async function ensureDataTab(B) {
  // The data tab is ALWAYS a tab we created in the background and can prove is
  // ours via (persisted targetId + matching window nonce). We NEVER adopt an
  // arbitrary marked tab and NEVER close a tab we can't verify — the user's
  // trading tab (which holds the broker/order ticket) must never be touched.
  const saved = tab.readDataTab();
  let tabs = await jsonGet("/json");

  // 1) Reuse path: the persisted tab still exists AND carries our nonce.
  if (saved.targetId && saved.nonce) {
    const t = tabs.find(x => x.id === saved.targetId && tab.isChart(x) && x.webSocketDebuggerUrl);
    if (t) {
      const P = await connect(t);
      if (await readNonce(P) === saved.nonce) { await waitApi(P); return P; }
      P.sock.close();   // id was recycled to some other tab — fall through to create
    }
  }

  // 2) Create a fresh dedicated background data tab. Stamp a new nonce so future
  //    runs (and the order tools' file-based exclusion) can identify it as ours.
  const nonce = crypto.randomUUID();
  const created = await B.call("Target.createTarget", { url: "https://www.tradingview.com/chart/", background: true });
  const tid = created.result && created.result.targetId;
  if (!tid) throw new Error("Target.createTarget failed");
  let page = null;
  for (let i = 0; i < 25; i++) { tabs = await jsonGet("/json"); page = tabs.find(t => t.id === tid); if (page && page.webSocketDebuggerUrl) break; await sleep(500); }
  if (!page) throw new Error("data tab did not appear");
  const P = await connect(page);
  await waitApi(P);
  await P.call("Runtime.evaluate", { expression: `window.${MARK}=true;window.${NONCE}=${JSON.stringify(nonce)};` });
  tab.writeDataTab(page.id, nonce);
  return P;
}

async function readSymbol(P, fullSym) {
  const ticker = fullSym.split(":").pop().toUpperCase();
  await P.call("Runtime.evaluate", { expression:
    `(function(){var c=window.TradingViewApi._activeChartWidgetWV.value();c.setSymbol(${JSON.stringify(fullSym)},{});try{c.setResolution(${JSON.stringify(RES)},function(){});}catch(e){}return 1;})()` });
  const readyExpr = `(function(){try{
    var ch=window.TradingViewApi._activeChartWidgetWV.value();var s=ch.getSeries();
    var loading=true;try{loading=s._series.isLoading();}catch(e){}
    var bc=-1;try{bc=s.barsCount();}catch(e){}
    return JSON.stringify({loading:loading,bc:bc,title:document.title});
  }catch(e){return JSON.stringify({err:String(e.message)});}})()`;
  const reTitle = new RegExp("^" + ticker.replace(/[.^$*+?()[\]{}|\\]/g, "\\$&") + "\\s+[0-9]");
  let ready = false;
  for (let i = 0; i < 40; i++) {            // ~20s
    const st = JSON.parse((await P.call("Runtime.evaluate", { expression: readyExpr, returnByValue: true })).result.result.value || "{}");
    if (st.loading === false && st.bc >= MIN && reTitle.test((st.title || "").toUpperCase())) { ready = true; break; }
    await sleep(500);
  }
  // Never extract on a timeout — that would return the PREVIOUS symbol's stale bars.
  if (!ready) return { symbol: fullSym, error: "load timeout (not ready)" };
  const extract = `(function(){try{
    var s=window.TradingViewApi._activeChartWidgetWV.value().getSeries();
    var pl=s._series.bars();var bars=[];
    pl.each(function(i,item){var v=item&&item.value?item.value:item;bars.push({time:v[0],open:v[1],high:v[2],low:v[3],close:v[4],volume:v[5]||0});return false;});
    return JSON.stringify({count:bars.length,bars:bars});
  }catch(e){return JSON.stringify({err:String(e.message)});}})()`;
  const out = JSON.parse((await P.call("Runtime.evaluate", { expression: extract, returnByValue: true })).result.result.value || "{}");
  if (out.err) return { symbol: fullSym, error: out.err };
  return { symbol: fullSym, count: out.count, bars: out.bars };
}

(async () => {
  if (!SYMBOLS.length) { emit({ results: [] }); return; }
  const ver = await jsonGet("/json/version");
  const burl = ver.webSocketDebuggerUrl.replace(/\/\/[^/]+\//, `//127.0.0.1:${PORT}/`);
  const B = mkConn(burl); await B.ready;
  let P;
  try { P = await ensureDataTab(B); }
  catch (e) { emit({ results: SYMBOLS.map(s => ({ symbol: s, error: "data tab: " + e.message })) }); return; }
  // Warm up: wait for the tab's initial chart to settle (not loading + bars loaded)
  // so the FIRST requested symbol isn't racing the tab's own boot/initial load.
  const warmExpr = `(function(){try{var s=window.TradingViewApi._activeChartWidgetWV.value().getSeries();
    var l=true;try{l=s._series.isLoading();}catch(e){}return JSON.stringify({loading:l,bc:s.barsCount(),title:document.title});}catch(e){return '{}';}})()`;
  for (let i = 0; i < 30; i++) {
    const st = JSON.parse((await P.call("Runtime.evaluate", { expression: warmExpr, returnByValue: true })).result.result.value || "{}");
    if (st.loading === false && st.bc >= MIN && /\s[0-9]/.test(st.title || "")) break;
    await sleep(500);
  }
  const results = [];
  for (const sym of SYMBOLS) {
    let r;
    try { r = await readSymbol(P, sym); }
    catch (e) { r = { symbol: sym, error: e.message }; }
    if (r.error) {                          // one retry (covers transient load races)
      try { r = await readSymbol(P, sym); } catch (e) { r = { symbol: sym, error: e.message }; }
    }
    results.push(r);
  }
  emit({ results });
})().catch(e => { console.error("ERR", e.message); process.exit(3); });
