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
  const tabs = await jget("/json");
  const trading = tab.pickTradingTab(tabs);
  if (!trading) { console.error("no trading tab found — refusing to do anything"); process.exit(2); }

  // Sweep EVERY chart tab for our data nonce in its window — close any that
  // carry it, tracked or not. This catches ORPHANED data tabs (window nonce
  // present but their tracking file was lost) which would otherwise leak and,
  // worse, get mis-picked as the trading tab by pickTradingTab (it only excludes
  // *tracked* ids). The user's trading tab never carries the nonce, so it's safe.
  const ver = await jget("/json/version");
  let closed = 0, kept = 0;
  for (const c of tabs.filter(x => tab.isChart(x) && x.webSocketDebuggerUrl)) {
    const P = mkConn(fix(c.webSocketDebuggerUrl)); await P.ready;
    const r = await P.call("Runtime.evaluate", { expression: 'String(window.__OPENING_DATA_NONCE__||"")', returnByValue: true });
    const nonce = (r.result && r.result.result && r.result.result.value) || null;
    P.sock.close();
    if (!nonce) { kept++; continue; }                       // no data nonce -> not ours (trading tab etc.)
    const B = mkConn(fix(ver.webSocketDebuggerUrl)); await B.ready;
    await B.call("Target.closeTarget", { targetId: c.id }); B.sock.close();
    const orphan = !files.some(f => { try { return JSON.parse(fs.readFileSync(path.join(LOGS, f), "utf8")).targetId === c.id; } catch (e) { return false; } });
    console.log("  CLOSED ", c.id.slice(0, 12), "(data nonce verified" + (orphan ? ", ORPHAN — was untracked" : "") + ")"); closed++;
  }
  // Clear all tracking files — every data tab we knew about is now closed.
  for (const f of files) { try { fs.unlinkSync(path.join(LOGS, f)); } catch (e) {} }
  console.log(`done: closed=${closed} kept=${kept} (trading tab ${trading.id.slice(0, 12)} protected)`);
})().catch(e => { console.error("ERR", e.message); process.exit(3); });
