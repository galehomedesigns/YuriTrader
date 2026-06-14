#!/usr/bin/env node
/**
 * tv_session_sync.js - verify the laptop trading Chrome is reachable + logged
 * in through the tunnel, and copy its TradingView sessionid into .env so the
 * watchlist REST sync shares that one session. Read-only except the .env write.
 *
 * Usage: node tv_session_sync.js [--port 9225]
 */
function arg(name, def) { const i = process.argv.indexOf("--" + name); return i === -1 ? def : process.argv[i + 1]; }
const PORT = arg("port", "9225");
const fs = require("fs");

(async () => {
  let tabs;
  try { tabs = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json(); }
  catch (e) { console.error(`NOT reachable on ${PORT} - is the laptop tunnel + Chrome up? (${e.message})`); process.exit(2); }
  const tv = tabs.find(x => x.url && x.url.includes("tradingview.com"));
  if (!tv) { console.error("reachable, but no TradingView tab open in the trading Chrome"); process.exit(2); }
  const ws = tv.webSocketDebuggerUrl.replace(/\/\/[^/]+\//, `//127.0.0.1:${PORT}/`);
  const sock = new WebSocket(ws);
  let id = 0; const w = {};
  sock.addEventListener("message", e => { const m = JSON.parse(e.data); if (m.id && w[m.id]) { w[m.id](m); delete w[m.id]; } });
  const call = (method, params = {}) => new Promise(res => { const i = ++id; w[i] = res; sock.send(JSON.stringify({ id: i, method, params })); });
  await new Promise(res => sock.addEventListener("open", res));
  await call("Network.enable");
  const u = await call("Runtime.evaluate", { expression: `(window.user&&(window.user.username||window.user.id))||"Guest"`, returnByValue: true });
  const user = u.result.result.value;
  const ck = await call("Network.getCookies", { urls: ["https://www.tradingview.com"] });
  const sid = (ck.result.cookies || []).find(c => c.name === "sessionid");
  if (user === "Guest" || !sid) { console.error(`reachable but NOT logged in (user=${user}). Log into TradingView in the trading Chrome.`); sock.close(); process.exit(3); }
  let env = fs.readFileSync("/home/tonygale/openclaw/.env", "utf8");
  if (/^TRADINGVIEW_SESSIONID=.*$/m.test(env)) env = env.replace(/^TRADINGVIEW_SESSIONID=.*$/m, "TRADINGVIEW_SESSIONID=" + sid.value);
  else env += "\nTRADINGVIEW_SESSIONID=" + sid.value + "\n";
  fs.writeFileSync("/home/tonygale/openclaw/.env", env);
  console.log(`OK - logged in as ${user}; .env sessionid synced (${sid.value.length} chars). Trading path ready on port ${PORT}.`);
  sock.close();
})().catch(e => { console.error("ERR", e.message); process.exit(3); });
