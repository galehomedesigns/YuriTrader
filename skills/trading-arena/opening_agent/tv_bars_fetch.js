#!/usr/bin/env node
/**
 * tv_bars_fetch.js - production bar feed: pull real-time 2-min OHLC bars for a
 * LIST of symbols off DEDICATED background TradingView data tabs (isolated from
 * the trading/order chart).
 *
 * Supports parallel fetching via --parallel N (default 3): opens N background
 * tabs and distributes symbols across them concurrently. Each tab is tracked
 * with its own nonce so order tools never stage onto them.
 *
 * Usage: node tv_bars_fetch.js --symbols NASDAQ:AAPL,NYSE:F [--min 200] [--res 2] [--port 9225] [--parallel 3]
 * Output (stdout): {"results":[{"symbol":"NASDAQ:AAPL","count":300,"bars":[...]}|{"symbol":...,"error":"..."}]}
 */
const tab = require("./tv_tab");
const crypto = require("crypto");
function arg(name, def) { const i = process.argv.indexOf("--" + name); return i === -1 ? def : process.argv[i + 1]; }
const PORT = arg("port", "9225");
const SYMBOLS = (arg("symbols", "") || "").split(",").map(s => s.trim()).filter(Boolean);
const MIN = parseInt(arg("min", "200"), 10);
const RES = arg("res", "2");
const PARALLEL = Math.max(1, Math.min(6, parseInt(arg("parallel", "3"), 10)));
const sleep = ms => new Promise(r => setTimeout(r, ms));

function mkConn(wsUrl) {
  const sock = new WebSocket(wsUrl); let id = 0; const w = {};
  sock.addEventListener("message", e => { const m = JSON.parse(e.data); if (m.id && w[m.id]) { w[m.id](m); delete w[m.id]; } });
  const call = (method, params = {}) => new Promise(r => { const i = ++id; w[i] = r; sock.send(JSON.stringify({ id: i, method, params })); });
  const ready = new Promise(r => sock.addEventListener("open", r));
  return { call, ready, sock };
}

async function jsonGet(path) { return (await (await fetch(`http://127.0.0.1:${PORT}${path}`)).json()); }

// Flush stdout BEFORE exiting - process.exit() truncates large unflushed pipe
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
  try {
    const r = await P.call("Runtime.evaluate", { expression: `String(window.${NONCE}||"")`, returnByValue: true });
    return (r.result && r.result.result && r.result.result.value) || null;
  } catch (e) { return null; }
}

async function waitApi(P) {
  for (let i = 0; i < 30; i++) {
    const r = await P.call("Runtime.evaluate", { expression: "!!(window.TradingViewApi && window.TradingViewApi._activeChartWidgetWV && window.TradingViewApi._activeChartWidgetWV.value())", returnByValue: true });
    if (r.result && r.result.result && r.result.result.value) return true;
    await sleep(300);
  }
  throw new Error("TradingViewApi not ready in data tab");
}

/** Create or reuse a single data tab. tabIdx distinguishes multiple parallel tabs. */
async function ensureDataTab(B, tabIdx) {
  const saved = tab.readDataTab(tabIdx);
  let tabs = await jsonGet("/json");

  // Reuse path: the persisted tab still exists AND carries our nonce.
  if (saved.targetId && saved.nonce) {
    const t = tabs.find(x => x.id === saved.targetId && tab.isChart(x) && x.webSocketDebuggerUrl);
    if (t) {
      const P = await connect(t);
      if (await readNonce(P) === saved.nonce) { await waitApi(P); return P; }
      P.sock.close();
    }
  }

  // Create a fresh dedicated background data tab.
  const nonce = crypto.randomUUID();
  const created = await B.call("Target.createTarget", { url: "https://www.tradingview.com/chart/", background: true });
  const tid = created.result && created.result.targetId;
  if (!tid) throw new Error("Target.createTarget failed");
  let page = null;
  for (let i = 0; i < 25; i++) { tabs = await jsonGet("/json"); page = tabs.find(t => t.id === tid); if (page && page.webSocketDebuggerUrl) break; await sleep(400); }
  if (!page) throw new Error("data tab did not appear");
  const P = await connect(page);
  await waitApi(P);
  await P.call("Runtime.evaluate", { expression: `window.${MARK}=true;window.${NONCE}=${JSON.stringify(nonce)};` });
  tab.writeDataTab(page.id, nonce, tabIdx);
  return P;
}

async function warmTab(P) {
  const warmExpr = `(function(){try{var s=window.TradingViewApi._activeChartWidgetWV.value().getSeries();
    var l=true;try{l=s._series.isLoading();}catch(e){}return JSON.stringify({loading:l,bc:s.barsCount(),title:document.title});}catch(e){return '{}';}})()`;
  for (let i = 0; i < 30; i++) {
    const st = JSON.parse((await P.call("Runtime.evaluate", { expression: warmExpr, returnByValue: true })).result.result.value || "{}");
    if (st.loading === false && st.bc >= MIN && /\s[0-9]/.test(st.title || "")) break;
    await sleep(200);
  }
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
  for (let i = 0; i < 40; i++) {
    const st = JSON.parse((await P.call("Runtime.evaluate", { expression: readyExpr, returnByValue: true })).result.result.value || "{}");
    if (st.loading === false && st.bc >= MIN && reTitle.test((st.title || "").toUpperCase())) { ready = true; break; }
    await sleep(200);
  }
  // Never extract on a timeout - that would return the PREVIOUS symbol's stale bars.
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

/** Process a batch of symbols on a single tab connection. */
async function processWorker(P, syms) {
  const results = [];
  for (const sym of syms) {
    let r;
    try { r = await readSymbol(P, sym); }
    catch (e) { r = { symbol: sym, error: e.message }; }
    if (r.error) {
      try { r = await readSymbol(P, sym); } catch (e) { r = { symbol: sym, error: e.message }; }
    }
    results.push(r);
  }
  return results;
}

(async () => {
  if (!SYMBOLS.length) { emit({ results: [] }); return; }
  const ver = await jsonGet("/json/version");
  const burl = ver.webSocketDebuggerUrl.replace(/\/\/[^/]+\//, `//127.0.0.1:${PORT}/`);
  const B = mkConn(burl); await B.ready;

  // Determine how many parallel tabs to use (cap at symbol count).
  const numTabs = Math.min(PARALLEL, SYMBOLS.length);

  // Create/reuse all data tabs concurrently.
  const tabConns = [];
  const tabErrors = [];
  const tabPromises = [];
  for (let i = 0; i < numTabs; i++) {
    tabPromises.push(
      ensureDataTab(B, i)
        .then(P => { tabConns[i] = P; })
        .catch(e => { tabErrors.push(e.message); })
    );
  }
  await Promise.all(tabPromises);

  // Filter to only working tabs.
  const working = tabConns.filter(Boolean);
  if (!working.length) {
    emit({ results: SYMBOLS.map(s => ({ symbol: s, error: "data tab: " + (tabErrors[0] || "unknown") })) });
    return;
  }

  // Warm all tabs in parallel.
  await Promise.all(working.map(P => warmTab(P)));

  // Distribute symbols round-robin across working tabs.
  const workerBuckets = working.map(() => []);
  SYMBOLS.forEach((sym, i) => workerBuckets[i % working.length].push(sym));

  // Run all workers in parallel.
  const workerResults = await Promise.all(
    working.map((P, i) => processWorker(P, workerBuckets[i]))
  );

  // Merge results preserving original symbol order.
  const resultMap = {};
  workerResults.flat().forEach(r => { resultMap[r.symbol] = r; });
  const results = SYMBOLS.map(s => resultMap[s] || { symbol: s, error: "not processed" });

  emit({ results });
})().catch(e => { console.error("ERR", e.message); process.exit(3); });
