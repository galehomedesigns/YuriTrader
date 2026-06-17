#!/usr/bin/env node
/**
 * tv_broker_health.js - check the Questrade<->TradingView BROKER link on the live
 * trading tab via CDP. Read-only: it never places/modifies anything (it may click
 * a Trading-Panel sub-tab to force the broker table to mount, same as tv_positions).
 *
 * Connected is proven by the presence of QUESTRADE.* broker DOM (the orders/
 * positions/account tables TradingView only mounts while the broker is linked).
 * When the link drops those unmount and TV shows a reconnect prompt instead.
 *
 * Usage: node tv_broker_health.js [--port 9225]
 * Output (stdout): {"connected":true|false,"broker":"Questrade"|null,"detail":"...","signals":[...]}
 */
function arg(name, def) { const i = process.argv.indexOf("--" + name); return i === -1 ? def : process.argv[i + 1]; }
const PORT = arg("port", "9225");

(async () => {
  let tabs;
  try { tabs = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json(); }
  catch (e) { console.log(JSON.stringify({ connected: false, broker: null, detail: "CDP unreachable on :" + PORT + " (" + e.message + ")", signals: [] })); process.exit(0); }
  const tv = require("./tv_tab").pickTradingTab(tabs);   // never the dedicated data tab
  if (!tv) { console.log(JSON.stringify({ connected: false, broker: null, detail: "no trading chart tab found", signals: [] })); process.exit(0); }
  const ws = tv.webSocketDebuggerUrl.replace(/\/\/[^/]+\//, `//127.0.0.1:${PORT}/`);
  const sock = new WebSocket(ws);
  let id = 0; const w = {};
  sock.addEventListener("message", e => { const m = JSON.parse(e.data); if (m.id && w[m.id]) { w[m.id](m); delete w[m.id]; } });
  const call = (method, params = {}) => new Promise(res => { const i = ++id; w[i] = res; sock.send(JSON.stringify({ id: i, method, params })); });
  await new Promise(res => sock.addEventListener("open", res));

  const expr = `(async () => {
    const sleep = ms => new Promise(r=>setTimeout(r,ms));
    const vis = el => !!(el && el.offsetParent !== null && el.getClientRects().length>0);
    const out = { connected:false, broker:null, detail:'', signals:[] };
    const qNodes = () => Array.from(document.querySelectorAll('[data-name]'))
      .filter(e => /^QUESTRADE\\./i.test(e.getAttribute('data-name')||''));
    // 1) passive: is Questrade broker DOM already mounted?
    let q = qNodes();
    // 2) if not, nudge the Trading Panel open (click a broker sub-tab) and re-check
    if (!q.length) {
      const tabBtn = Array.from(document.querySelectorAll('button,[role=button],[role=tab]'))
        .find(b=>vis(b)&&/^(account|orders|positions)\\b/i.test((b.innerText||'').trim()));
      if (tabBtn) { tabBtn.click(); await sleep(900); q = qNodes(); }
    }
    if (q.length) { out.connected = true; out.signals.push('QUESTRADE.* DOM x'+q.length); }
    // 3) explicit disconnect / reconnect prompt = hard DOWN signal
    const down = Array.from(document.querySelectorAll('button,[role=button],a,span,div'))
      .filter(e=>vis(e))
      .find(e=>{ const t=(e.innerText||'').trim().toLowerCase();
        return t && t.length<60 && /(^connect$|reconnect|trading is not available|broker .*disconnect|session expired|connect to)/.test(t); });
    // 4) broker name label (best-effort, for the alert text)
    const lbl = Array.from(document.querySelectorAll('*'))
      .find(e=>vis(e)&&e.childElementCount===0&&/questrade/i.test(e.textContent||'')&&(e.textContent||'').length<40);
    if (lbl) out.broker = 'Questrade';
    // decision
    if (down) { out.connected=false; out.detail='reconnect prompt visible: "'+((down.innerText||'').trim().slice(0,50))+'"'; out.signals.push('prompt'); }
    else if (out.connected) { out.detail='Questrade broker connected'; }
    else { out.connected=false; out.detail='no QUESTRADE broker DOM (panel closed or link down)'; }
    return JSON.stringify(out);
  })()`;
  let val;
  try {
    const r = await call("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    val = r.result && r.result.result ? r.result.result.value : null;
  } catch (e) { val = JSON.stringify({ connected: false, broker: null, detail: "evaluate failed: " + e.message, signals: [] }); }
  console.log(val || JSON.stringify({ connected: false, broker: null, detail: "no result", signals: [] }));
  sock.close();
  process.exit(0);
})().catch(e => { console.log(JSON.stringify({ connected: false, broker: null, detail: "ERR " + e.message, signals: [] })); process.exit(0); });
