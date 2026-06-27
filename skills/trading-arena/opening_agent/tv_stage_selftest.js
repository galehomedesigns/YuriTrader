#!/usr/bin/env node
/**
 * tv_stage_selftest.js — PRE-OPEN CANARY for the order-staging DOM path.
 *
 * The failure that costs trades at 9:32 is that the TradingView order ticket's
 * order-type tabs / stop-loss section can't be located (the Windows-Chrome CDP
 * innerText-drop on a cold ticket; or a TV DOM change). It was only ever
 * discovered LIVE, when a real setup fired. This canary exercises the exact
 * finders BEFORE the open — opens the ticket and verifies the 'Stop' order-type
 * tab, the 'Stop loss' section, and the submit button are all locatable — and
 * NEVER submits anything. preopen_check.py runs it at 8:30 + 9:15 ET and alerts
 * if it fails, so the path can be re-warmed/fixed before 9:30.
 *
 * Usage: node tv_stage_selftest.js [--port 9225]
 * Output (stdout JSON): {"ok":true|false,"checks":{...},"detail":"...","sym":"..."}
 * Exit 0 always (the JSON carries pass/fail); non-JSON/exit!=0 = infra failure.
 */
const tab = require("./tv_tab");
function arg(name, def) { const i = process.argv.indexOf("--" + name); return i === -1 ? def : process.argv[i + 1]; }
const PORT = arg("port", "9225");
const sleep = ms => new Promise(r => setTimeout(r, ms));

function mkConn(wsUrl) {
  const sock = new WebSocket(wsUrl); let id = 0; const w = {};
  sock.addEventListener("message", e => { const m = JSON.parse(e.data); if (m.id && w[m.id]) { w[m.id](m); delete w[m.id]; } });
  const call = (method, params = {}) => new Promise(r => { const i = ++id; w[i] = r; sock.send(JSON.stringify({ id: i, method, params })); });
  return { call, ready: new Promise(r => sock.addEventListener("open", r)), sock };
}
const jsonGet = async p => (await (await fetch(`http://127.0.0.1:${PORT}${p}`)).json());
function emit(o) { process.stdout.write(JSON.stringify(o) + "\n", () => process.exit(0)); }

// Runs IN the page: open the ticket, then LOCATE (never click submit) the three
// elements staging depends on. Uses textContent (not innerText) — the same fix the
// real finders use, so a pass here predicts a real stage will find its elements.
const PAGE_FN = `async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const vis = el => !!(el && el.offsetParent !== null && el.getClientRects().length > 0);
  const tx = el => ((el.textContent || el.innerText || '').trim().toLowerCase());
  const orderTypeTabs = () => Array.from(document.querySelectorAll('button,[role=button],[role=tab],[data-name*=order-type]'))
    .filter(vis).filter(b => ['market','limit','stop','stop limit'].includes(tx(b)));
  // ensureTicketOpen: if no order-type tabs are showing, click the toolbar "Trade".
  for (let a = 0; a < 4 && orderTypeTabs().length === 0; a++) {
    const trade = Array.from(document.querySelectorAll('button,[role=button]')).find(b => vis(b) && tx(b) === 'trade');
    if (trade) { trade.click(); }
    await sleep(700);
  }
  const findType = t => orderTypeTabs().find(b => tx(b) === t || tx(b).startsWith(t));
  // Put the ticket into the real entry shape (Stop + Buy) so the stop-loss probe
  // mirrors a live stage. Never submits.
  const stopTab = findType('stop'); if (stopTab) { stopTab.click(); await sleep(450); }
  const buy = document.querySelector('[data-name=side-control-buy]'); if (buy) { buy.click(); await sleep(350); }
  // Exercise the EXACT attach path the queue uses: switch-anchored toggle + the
  // SL price input located by document order after the switch. This is what
  // actually failed on 2026-06-26; the old label-only check missed it.
  const slSwitch = () => Array.from(document.querySelectorAll('[role=switch],input[type=checkbox]')).filter(vis)
    .find(s => { let p=s,h=0; while(p&&h<6){ const t=(p.textContent||'').toLowerCase(); const mSL=t.search(/stop\\s*loss/),mTP=t.search(/take\\s*profit/); if(mSL>=0||mTP>=0) return mSL>=0&&(mTP<0||mSL<mTP); p=p.parentElement; h++; } return false; });
  let attachable = false, restoreOff = false;
  let sw = slSwitch();
  if (!sw) { // try expanding Exits once
    const ex = Array.from(document.querySelectorAll('*')).find(e => vis(e) && tx(e) === 'exits' && (e.textContent||'').length < 12);
    if (ex) { (ex.closest('[role=button]')||ex.parentElement||ex).click(); await sleep(650); sw = slSwitch(); }
  }
  if (sw) {
    const wasOff = sw.checked !== true;
    if (wasOff) { sw.click(); await sleep(450); sw = slSwitch(); }
    if (sw && sw.checked === true) {
      const decs = Array.from(document.querySelectorAll('input')).filter(e => vis(e) && e.getAttribute('inputmode')==='decimal');
      attachable = !!decs.find(d => (sw.compareDocumentPosition(d) & 4)); // SL price input follows the switch
      if (wasOff && sw) { sw.click(); restoreOff = true; await sleep(300); } // leave it as we found it
    }
  }
  const submit = () => document.querySelector('[data-name=place-and-modify-button]')
    || Array.from(document.querySelectorAll('button,[role=button]')).find(b => vis(b) && /^(buy|sell)\\s/.test(tx(b)));
  const checks = {
    ticketOpen: orderTypeTabs().length > 0,
    orderTypeStop: !!findType('stop'),
    stopLossAttachable: attachable,
    submitButton: !!submit(),
  };
  const ok = checks.ticketOpen && checks.orderTypeStop && checks.stopLossAttachable && checks.submitButton;
  return JSON.stringify({ ok, checks, sym: document.title.slice(0, 24),
    detail: ok ? 'order-staging DOM path healthy (stop-loss attach verified)' :
      'staging path BROKEN: ' + Object.entries(checks).filter(([,v]) => !v).map(([k]) => k).join(', ') });
}`;

(async () => {
  let tabs;
  try { tabs = await jsonGet("/json"); }
  catch (e) { return emit({ ok: false, checks: {}, detail: "CDP unreachable on :" + PORT + " (" + e.message + ")", sym: null }); }
  const tv = tab.pickTradingTab(tabs);
  if (!tv || !tv.webSocketDebuggerUrl) return emit({ ok: false, checks: {}, detail: "no trading chart tab found", sym: null });
  const P = mkConn(tv.webSocketDebuggerUrl.replace(/\/\/[^/]+\//, `//127.0.0.1:${PORT}/`));
  await P.ready; await P.call("Runtime.enable");
  let out;
  try {
    const r = await P.call("Runtime.evaluate", { expression: "(" + PAGE_FN + ")()", returnByValue: true, awaitPromise: true });
    out = JSON.parse((r.result && r.result.result && r.result.result.value) || '{"ok":false,"checks":{},"detail":"no result"}');
  } catch (e) { out = { ok: false, checks: {}, detail: "evaluate failed: " + e.message, sym: null }; }
  P.sock.close();
  emit(out);
})().catch(e => emit({ ok: false, checks: {}, detail: "ERR " + e.message, sym: null }));
