#!/usr/bin/env node
/**
 * tv_order.js - STAGE-ONLY TradingView order driver via Chrome DevTools Protocol.
 *
 * Fills the TradingView order ticket (side / type / price / qty) on the
 * connected Questrade broker, reads the values back to verify, and then opens
 * the order CONFIRMATION dialog. It NEVER clicks the final "Send Order" — a
 * human approves every order. If the confirmation does not appear after the
 * submit click (e.g. instant-order mode), it reports that loudly.
 *
 * Connects through the reverse tunnel (GX10:PORT -> laptop Chrome:9222).
 *
 * Usage:
 *   node tv_order.js --side buy --type stop --price 350 --qty 1 [--port 9224]
 *   node tv_order.js ... --fill-only      # fill the ticket but do NOT open confirm
 *
 * Exit 0 = staged (confirmation open, awaiting human) or filled (--fill-only).
 * Exit non-zero = a field/selector failed; nothing was opened.
 */

function arg(name, def) {
  const i = process.argv.indexOf("--" + name);
  if (i === -1) return def;
  const v = process.argv[i + 1];
  return (v === undefined || v.startsWith("--")) ? true : v;
}

const PORT = arg("port", "9224");
const SIDE = String(arg("side", "buy")).toLowerCase();     // buy | sell
const TYPE = String(arg("type", "stop")).toLowerCase();    // market | limit | stop | stop limit
const PRICE = arg("price", null);
const QTY = arg("qty", null);
const FILL_ONLY = arg("fill-only", false);
const EXPECT_SYMBOL = arg("expect-symbol", null);
const STOP = arg("stop", null);

if (!["buy", "sell"].includes(SIDE)) { console.error("side must be buy|sell"); process.exit(1); }
if (QTY == null) { console.error("--qty required"); process.exit(1); }
if (TYPE !== "market" && PRICE == null) { console.error("--price required for " + TYPE); process.exit(1); }

// The DOM routine that runs inside the TradingView page.
const PAGE_FN = `async (opts) => {
  const log = [];
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const vis = el => !!(el && el.offsetParent !== null && el.getClientRects().length > 0);
  const chartSymbol = (window.TradingViewApi && window.TradingViewApi._activeChartWidgetWV)
    ? window.TradingViewApi._activeChartWidgetWV.value().symbol() : null;
  log.push('chart symbol = ' + chartSymbol);
  // Symbol safety: if caller passed an expected symbol, abort on mismatch.
  if (opts.expectSymbol && chartSymbol && String(chartSymbol).toUpperCase().indexOf(String(opts.expectSymbol).toUpperCase()) === -1) {
    return { ok: false, chartSymbol, log: [...log, 'SYMBOL MISMATCH: expected ' + opts.expectSymbol + ' but chart is ' + chartSymbol + ' - aborting'] };
  }
  function setInput(el, val) {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement : HTMLInputElement;
    const setter = Object.getOwnPropertyDescriptor(proto.prototype, 'value').set;
    el.focus();
    setter.call(el, String(val));
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    if (el.blur) el.blur();
  }
  function labelFor(inp) {
    let lab = inp.getAttribute('aria-label') || inp.placeholder || '';
    let n = inp, hops = 0;
    while (n && hops < 4 && !lab) {
      n = n.parentElement; hops++; if (!n) break;
      const txt = Array.from(n.childNodes).filter(c => c.nodeType === 3).map(c => c.textContent.trim()).filter(Boolean).join(' ');
      const lblEl = n.querySelector('label,[class*=label],[class*=Label]');
      lab = ((lblEl ? (lblEl.innerText || '') : '') + ' ' + txt).replace(/\\s+/g, ' ').trim();
    }
    return lab;
  }
  // Find a bracket section (e.g. "Stop loss") = the row holding its switch + price.
  function findSection(word) {
    let cands = Array.from(document.querySelectorAll('*')).filter(e => vis(e) && (e.innerText || '').trim().toLowerCase().startsWith(word.toLowerCase()) && (e.innerText || '').length < 60);
    cands.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
    let lab = cands[0]; if (!lab) return null;
    let row = lab, h = 0;
    while (row && h < 6) {
      const t = row.querySelector('[class*=switchContainer],input[type=checkbox],[role=switch]');
      const p = Array.from(row.querySelectorAll('input')).find(i => i.getAttribute('inputmode') === 'decimal');
      if (t && p) return { toggle: t, price: p };
      row = row.parentElement; h++;
    }
    return null;
  }
  function switchOn(t) {
    const a = t.getAttribute('aria-checked'); if (a != null) return a === 'true';
    const inp = t.querySelector('input[type=checkbox]'); if (inp) return inp.checked;
    return /checked|enabled|active/i.test(t.className) || !!t.querySelector('[class*=checked],[aria-checked=true]');
  }

  // 1) order type
  const tab = Array.from(document.querySelectorAll('button,[role=button],[role=tab]'))
    .find(b => vis(b) && (b.innerText || '').trim().toLowerCase() === opts.type);
  if (!tab) return { ok: false, log: ['ORDER-TYPE TAB NOT FOUND: ' + opts.type] };
  tab.click(); log.push('order type -> ' + opts.type);
  await sleep(450);

  // 2) side
  const sideEl = document.querySelector('[data-name=side-control-' + opts.side + ']');
  if (!sideEl) return { ok: false, log: [...log, 'SIDE CONTROL NOT FOUND: ' + opts.side] };
  sideEl.click(); log.push('side -> ' + opts.side);
  await sleep(350);

  // 3) price (stop/limit trigger) - first visible text input whose label is Price
  if (opts.price != null) {
    const priceEl = Array.from(document.querySelectorAll('input'))
      .filter(e => vis(e) && e.type === 'text')
      .find(e => /^price/i.test(labelFor(e)));
    if (!priceEl) return { ok: false, log: [...log, 'PRICE INPUT NOT FOUND'] };
    setInput(priceEl, opts.price); log.push('price -> ' + opts.price);
    await sleep(250);
  }

  // 4) quantity - the decimal input right AFTER the Price field. The qtyEl
  //    element is a display DIV (title="Quantity") that mirrors the live qty,
  //    so after setting we verify against it and abort if it didn't update
  //    (means we hit the wrong field) rather than guess.
  const decInputs = Array.from(document.querySelectorAll('input')).filter(e => vis(e) && e.getAttribute('inputmode') === 'decimal');
  const priceIdx = decInputs.findIndex(e => /^price/i.test(labelFor(e)));
  const qtyInput = (opts.price == null || priceIdx < 0) ? decInputs[0] : decInputs[priceIdx + 1];
  if (!qtyInput) return { ok: false, log: [...log, 'QTY INPUT NOT FOUND'] };
  setInput(qtyInput, opts.qty); log.push('qty -> ' + opts.qty);
  await sleep(400);
  const qtyMirror = document.querySelector('[data-name=qtyEl]');
  const mirrorTxt = qtyMirror ? (qtyMirror.innerText || '').trim() : null;
  if (mirrorTxt !== String(opts.qty)) return { ok: false, log: [...log, 'QTY MIRROR MISMATCH: set ' + opts.qty + ' but qtyEl shows ' + mirrorTxt + ' - wrong field, aborting'] };
  log.push('qty confirmed via qtyEl mirror = ' + mirrorTxt);

  // 5b) attach protective stop-loss (bracket) if requested
  if (opts.stop != null) {
    let sl = findSection('Stop loss');
    if (!sl) {
      // Exits got collapsed by the order-type re-render. We don't know its
      // current state, so toggle the "Exits" header up to twice until the
      // Stop-loss section actually appears (handles both collapsed/expanded).
      const findExits = () => Array.from(document.querySelectorAll('*')).filter(e => vis(e) && (e.innerText || '').trim().toLowerCase() === 'exits' && (e.innerText || '').length < 12)[0];
      for (let attempt = 0; attempt < 2 && !sl; attempt++) {
        const hdr = findExits();
        if (!hdr) break;
        (hdr.closest('[role=button]') || hdr.parentElement || hdr).click();
        await sleep(650);
        sl = findSection('Stop loss');
        log.push('toggled Exits (attempt ' + (attempt + 1) + '), SL found=' + !!sl);
      }
    }
    if (!sl) return { ok: false, chartSymbol, log: [...log, 'STOP-LOSS SECTION NOT FOUND (even after trying to expand Exits)'] };
    // The switch state lives in an inner <input type=checkbox>; click THAT (not
    // the container), once, only if currently off, then verify .checked.
    const cb = sl.toggle.querySelector('input[type=checkbox]') || (sl.toggle.tagName === 'INPUT' ? sl.toggle : null);
    if (!cb) return { ok: false, chartSymbol, log: [...log, 'STOP-LOSS CHECKBOX NOT FOUND'] };
    if (!cb.checked) { cb.click(); await sleep(400); }
    if (!cb.checked) return { ok: false, chartSymbol, log: [...log, 'STOP-LOSS TOGGLE would not enable - aborting'] };
    setInput(sl.price, opts.stop); await sleep(300);
    log.push('stop-loss -> ' + opts.stop + ' (toggle on=' + cb.checked + ', price=' + sl.price.value + ')');
  }

  // 5) read back for verification
  const priceBack = (() => {
    const e = Array.from(document.querySelectorAll('input')).filter(x => vis(x) && x.type === 'text').find(x => /^price/i.test(labelFor(x)));
    return e ? e.value : null;
  })();
  const qtyBack = qtyInput.value;
  log.push('readback price=' + priceBack + ' qty=' + qtyBack);

  // verify before opening anything
  if (String(qtyBack).trim() === '' || Number(qtyBack) <= 0) return { ok: false, log: [...log, 'QTY READBACK INVALID - not opening'] };
  if (opts.price != null && (priceBack == null || String(priceBack).trim() === '')) return { ok: false, log: [...log, 'PRICE READBACK INVALID - not opening'] };

  if (opts.fillOnly) return { ok: true, staged: false, filled: true, chartSymbol, log, priceBack, qtyBack };

  // 6) submit -> opens confirmation. The right panel's submit is the
  //    place-and-modify-button ("Start creating order" / "Buy <sym>"), NOT the
  //    floating buy-order-button. This does NOT send; a confirmation follows.
  const btn = document.querySelector('[data-name=place-and-modify-button]');
  if (!btn) return { ok: false, chartSymbol, log: [...log, 'SUBMIT BUTTON (place-and-modify-button) NOT FOUND'] };
  const btnBefore = (btn.innerText || '').replace(/\\s+/g, ' ').trim();
  btn.click(); log.push('clicked submit (was: "' + btnBefore + '")');

  // 7) poll up to ~4s for the confirmation (Send Order / Cancel) to render. If
  //    it never appears, report the submit button's text so we can see the state.
  let confirmVisible = false, rejection = null;
  for (let k = 0; k < 10 && !confirmVisible && !rejection; k++) {
    await sleep(400);
    const sb = Array.from(document.querySelectorAll('button,[role=button]')).find(b => /send order|confirm/i.test(b.innerText || ''));
    confirmVisible = vis(sb);
    const rej = Array.from(document.querySelectorAll('*')).find(e => vis(e) && /was rejected|both marketable|order rejected|please modify/i.test(e.innerText || '') && (e.innerText || '').length < 220);
    if (rej) rejection = (rej.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 180);
  }
  if (rejection) return { ok: false, chartSymbol, log: [...log, 'ORDER REJECTED by broker: ' + rejection] };
  if (!confirmVisible) {
    const after = (document.querySelector('[data-name=place-and-modify-button]') || {}).innerText || '';
    return { ok: false, chartSymbol, confirmVisible, log: [...log, 'NO confirmation after submit; submit button now reads: "' + after.replace(/\\s+/g, ' ').trim() + '"'] };
  }
  log.push('confirmation dialog is open - awaiting human');
  return { ok: true, staged: true, confirmVisible: true, chartSymbol, log, priceBack, qtyBack };
}`;

(async () => {
  let tabs;
  try {
    tabs = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
  } catch (e) {
    console.error(`Cannot reach CDP at 127.0.0.1:${PORT} (tunnel down?): ${e.message}`);
    process.exit(2);
  }
  const tv = require("./tv_tab").pickTradingTab(tabs);   // never the dedicated data tab
  if (!tv) { console.error("No TradingView tab found"); process.exit(2); }
  const ws = tv.webSocketDebuggerUrl.replace(/\/\/[^/]+\//, `//127.0.0.1:${PORT}/`);
  const sock = new WebSocket(ws);
  let id = 0; const waiters = {};
  sock.addEventListener("message", e => { const m = JSON.parse(e.data); if (m.id && waiters[m.id]) { waiters[m.id](m); delete waiters[m.id]; } });
  const call = (method, params = {}) => new Promise(res => { const i = ++id; waiters[i] = res; sock.send(JSON.stringify({ id: i, method, params })); });
  await new Promise((res, rej) => { sock.addEventListener("open", res); sock.addEventListener("error", () => rej(new Error("ws failed"))); });

  const opts = { side: SIDE, type: TYPE, price: PRICE === null ? null : String(PRICE), qty: String(QTY), fillOnly: !!FILL_ONLY, expectSymbol: (EXPECT_SYMBOL && EXPECT_SYMBOL !== true) ? EXPECT_SYMBOL : null, stop: (STOP && STOP !== true) ? String(STOP) : null };
  const expr = `(${PAGE_FN})(${JSON.stringify(opts)})`;
  const res = await call("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
  sock.close();

  if (res.result && res.result.exceptionDetails) {
    console.error("page exception:", JSON.stringify(res.result.exceptionDetails));
    process.exit(3);
  }
  const out = res.result && res.result.result && res.result.result.value;
  if (out) {
    console.log(JSON.stringify(out, null, 1));
    process.exit(out.ok ? 0 : 3);
  } else {
    console.error("eval error:", JSON.stringify(res.result || res));
    process.exit(3);
  }
})().catch(e => { console.error("ERR", e.message); process.exit(3); });
