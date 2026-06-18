#!/usr/bin/env node
/**
 * tv_data_teardown.js - safely close the dedicated background DATA tabs created
 * by tv_bars_fetch.js, WITHOUT ever touching the user's trading tab.
 *
 * Safety: a tab is only closed if (a) it is one of the tracked data-tab ids,
 * (b) it is NOT the tab pickTradingTab() resolves to, and (c) its live
 * window.__OPENING_DATA_NONCE__ matches the nonce we persisted for it. Any tab
 * that fails the nonce proof is left open. After closing, the tracking file is
 * removed so the next fetch starts clean.
 *
 * Usage: node tv_data_teardown.js [--port 9225]
 */
const fs = require("fs");
const path = require("path");
const tab = require("./tv_tab");
function arg(n, d) { const i = process.argv.indexOf("--" + n); return i > -1 ? process.argv[i + 1] : d; }
const PORT = arg("port", process.env.OPENING_TV_CDP_PORT || "9225");
const LOGS = "/home/tonygale/openclaw/skills/trading-arena/logs";

function mkConn(wsUrl) {
  const sock = new WebSocket(wsUrl); let id = 0; const w = {};
  sock.addEventListener("message", e => { const m = JSON.parse(e.data); if (m.id && w[m.id]) { w[m.id](m); delete w[m.id]; } });
  const call = (method, params = {}) => new Promise(r => { const i = ++id; w[i] = r; sock.send(JSON.stringify({ id: i, method, params })); });
  const ready = new Promise(r => sock.addEventListener("open", r));
  return { call, ready, sock };
}
const jget = p => fetch(`http://127.0.0.1:${PORT}${p}`).then(r => r.json());
const fix = u => u.replace(/\/\/[^/]+\//, `//127.0.0.1:${PORT}/`);

(async () => {
  const files = fs.readdirSync(LOGS).filter(f => f.startsWith("tv_data_tab") && f.endsWith(".json"));
  const tracked = files.map(f => { try { return { f, ...JSON.parse(fs.readFileSync(path.join(LOGS, f), "utf8")) }; } catch (e) { return { f }; } }).filter(x => x.targetId);
  const tabs = await jget("/json");
  const trading = tab.pickTradingTab(tabs);
  if (!trading) { console.error("no trading tab found — refusing to do anything"); process.exit(2); }
  console.log("trading tab (PROTECTED):", trading.id.slice(0, 12));

  let closed = 0, kept = 0;
  for (const t of tracked) {
    const fp = path.join(LOGS, t.f);
    if (t.targetId === trading.id) { console.log("  REFUSE", t.targetId.slice(0, 12), "— is the trading tab"); kept++; continue; }
    const live = tabs.find(x => x.id === t.targetId && x.webSocketDebuggerUrl && tab.isChart(x));
    if (!live) { console.log("  gone   ", t.targetId.slice(0, 12), "— already closed; clearing file"); try { fs.unlinkSync(fp); } catch (e) {} continue; }
    const P = mkConn(fix(live.webSocketDebuggerUrl)); await P.ready;
    const r = await P.call("Runtime.evaluate", { expression: 'String(window.__OPENING_DATA_NONCE__||"")', returnByValue: true });
    const liveNonce = (r.result && r.result.result && r.result.result.value) || null;
    P.sock.close();
    if (!t.nonce || liveNonce !== t.nonce) { console.log("  KEEP   ", t.targetId.slice(0, 12), "— nonce mismatch, not provably ours"); kept++; continue; }
    const ver = await jget("/json/version");
    const B = mkConn(fix(ver.webSocketDebuggerUrl)); await B.ready;
    await B.call("Target.closeTarget", { targetId: t.targetId }); B.sock.close();
    try { fs.unlinkSync(fp); } catch (e) {}
    console.log("  CLOSED ", t.targetId.slice(0, 12), "(nonce verified)"); closed++;
  }
  console.log(`done: closed=${closed} kept=${kept}`);
})().catch(e => { console.error("ERR", e.message); process.exit(3); });
