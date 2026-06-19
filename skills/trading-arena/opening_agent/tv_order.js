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
  // Char-drop-tolerant matching: the Windows Chrome intermittently drops chars
  // from innerText via CDP (e.g. "Stop loss"->"Stop lo", "Session"->"Se ion").
  // These check if text's chars appear as a subsequence of target (dropped chars
  // = chars present in target but missing from rendered text).
  const _isSubseq = (short, long) => {
    let li = 0;
    for (let si = 0; si < short.length; si++) {
      while (li < long.length && long[li] !== short[si]) li++;
      if (li >= long.length) return false;
      li++;
    }
    return true;
  };
  const fuzzyStartsWith = (text, target) => {
    const t = (text || '').trim().toLowerCase();
    const g = target.toLowerCase();
    if (t.startsWith(g)) return true;
    const prefix = t.slice(0, Math.ceil(g.length * 1.5));
    return prefix.length >= g.length * 0.5 && _isSubseq(prefix, g);
  };
  const fuzzyEquals = (text, target) => {
    const t = (text || '').trim().toLowerCase();
    const g = target.toLowerCase();
    if (t === g) return true;
    return t.length >= g.length * 0.5 && t.length <= g.length * 1.5 && _isSubseq(t, g);
  };
  // HARD GUARD: never stage on a DATA tab. An orphaned data tab (our nonce in
  // window, tracking file lost) can be mis-picked as the trading tab; an order
  // staged there never reaches the broker. The real trading tab never carries
  // this nonce. Run tv_data_teardown.js to clear orphans.
  if (window.__OPENING_DATA_NONCE__) {
    return { ok: false, log: ['REFUSING: connected to a DATA tab (has __OPENING_DATA_NONCE__), not the trading tab. Run tv_data_teardown.js to close orphaned data tabs.'] };
  }
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
    let cands = Array.from(document.querySelectorAll('*')).filter(e => vis(e) && fuzzyStartsWith(e.innerText, word) && (e.innerText || '').length < 60);
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

  // Resilient order-type tab finder: tries exact match first, then startsWith,
  // then includes. Handles Pro UI variations like "Stop Order", "Limit Order",
  // extra whitespace, different casing. Retries up to 3 times with delays to
  // handle late-rendering panels after symbol switch.
  async function findOrderTypeTab(targetType) {
    const norm = targetType.trim().toLowerCase();
    // Map shorthand to possible label variations
    const aliases = {
      'stop': ['stop', 'stop order', 'stp'],
      'limit': ['limit', 'limit order', 'lmt'],
      'market': ['market', 'market order', 'mkt'],
      'stop limit': ['stop limit', 'stop-limit', 'stop limit order', 'stplmt']
    };
    const candidates = aliases[norm] || [norm];

    for (let attempt = 0; attempt < 3; attempt++) {
      const allBtns = Array.from(document.querySelectorAll('button,[role=button],[role=tab],[data-name*=order-type]'));
      const visBtns = allBtns.filter(b => vis(b));

      // Phase 1: exact match on trimmed lowercase innerText
      for (const b of visBtns) {
        const txt = (b.innerText || '').trim().toLowerCase();
        if (candidates.includes(txt)) return b;
      }

      // Phase 2: innerText starts with one of our candidates
      for (const b of visBtns) {
        const txt = (b.innerText || '').trim().toLowerCase();
        for (const c of candidates) {
          if (txt.startsWith(c)) return b;
        }
      }

      // Phase 3: data-name attribute contains the type
      for (const b of visBtns) {
        const dn = (b.getAttribute('data-name') || '').toLowerCase();
        if (dn.includes(norm) || dn.includes(norm.replace(' ', '-')) || dn.includes(norm.replace(' ', '_'))) return b;
      }

      // Phase 4: aria-label contains the type
      for (const b of visBtns) {
        const al = (b.getAttribute('aria-label') || '').toLowerCase();
        for (const c of candidates) {
          if (al.includes(c)) return b;
        }
      }

      if (attempt < 2) await sleep(600);
    }
    return null;
  }

  // Resilient submit button finder: tries multiple selectors with retries.
  async function findSubmitButton() {
    for (let attempt = 0; attempt < 3; attempt++) {
      // Primary: data-name attribute
      let btn = document.querySelector('[data-name=place-and-modify-button]');
      if (btn && vis(btn)) return btn;

      // Fallback 1: data-name contains relevant keywords
      btn = document.querySelector('[data-name*=place-button],[data-name*=submit-button],[data-name*=order-button]');
      if (btn && vis(btn)) return btn;

      // Fallback 2: button whose text matches expected patterns
      const allBtns = Array.from(document.querySelectorAll('button,[role=button]')).filter(b => vis(b));
      btn = allBtns.find(b => {
        const txt = (b.innerText || '').trim().toLowerCase();
        return /^(buy|sell)\\s/.test(txt) && !/send order|cancel/i.test(txt);
      });
      if (btn) return btn;

      // Fallback 3: any button with "place" or "review" in text
      btn = allBtns.find(b => /place|review order|create order/i.test((b.innerText || '').trim()));
      if (btn) return btn;

      if (attempt < 2) await sleep(500);
    }
    return null;
  }

  // Ensure the order ENTRY ticket is open. On a fresh chart the Questrade broker
  // panel shows Positions/Orders but the entry ticket (order-type tabs, side
  // controls, place button) stays closed until the toolbar "Trade" button is
  // clicked. Every step below assumes it's open, so open it first if needed.
  async function ensureTicketOpen() {
    const up = () => !!(document.querySelector('[data-name=place-and-modify-button]') || document.querySelector('[data-name^=side-control]'));
    if (up()) return true;
    for (let attempt = 0; attempt < 3 && !up(); attempt++) {
      const trade = Array.from(document.querySelectorAll('button,[role=button]')).find(b => vis(b) && fuzzyEquals(b.innerText, 'trade'));
      if (trade) trade.click();
      for (let k = 0; k < 12 && !up(); k++) await sleep(300);   // ~3.6s
    }
    return up();
  }

  // 0) make sure the entry ticket is actually open before locating its controls
  if (!(await ensureTicketOpen())) {
    return { ok: false, chartSymbol, log: ['ORDER TICKET NOT OPEN: clicked "Trade" but the order entry form never appeared'] };
  }

  // 1) order type (with retries and fuzzy matching)
  const tab = await findOrderTypeTab(opts.type);
  if (!tab) {
    const allBtns = Array.from(document.querySelectorAll('button,[role=button],[role=tab]')).filter(b => vis(b)).map(b => (b.innerText||'').trim().toLowerCase().slice(0,30));
    return { ok: false, log: ['ORDER-TYPE TAB NOT FOUND: ' + opts.type + ' | visible buttons: ' + JSON.stringify(allBtns.slice(0,15))] };
  }
  tab.click(); log.push('order type -> ' + opts.type);
  await sleep(450);

  // 2) side
  let sideEl = document.querySelector('[data-name=side-control-' + opts.side + ']');
  if (!sideEl) {
    // Fallback: find by aria-label or text content
    await sleep(300);
    sideEl = Array.from(document.querySelectorAll('button,[role=button],[role=tab]')).find(b => vis(b) && (b.getAttribute('data-name')||'').includes('side') && (b.innerText||'').trim().toLowerCase() === opts.side);
  }
  if (!sideEl) return { ok: false, log: [...log, 'SIDE CONTROL NOT FOUND: ' + opts.side] };
  sideEl.click(); log.push('side -> ' + opts.side);
  await sleep(350);

  // 3) price (stop/limit trigger) - first visible text input whose label is Price
  if (opts.price != null) {
    let priceEl = null;
    for (let attempt = 0; attempt < 3 && !priceEl; attempt++) {
      priceEl = Array.from(document.querySelectorAll('input'))
        .filter(e => vis(e) && e.type === 'text')
        .find(e => /^price/i.test(labelFor(e)));
      if (!priceEl) {
        // Fallback: decimal input that isn't qty
        priceEl = Array.from(document.querySelectorAll('input'))
          .filter(e => vis(e) && e.getAttribute('inputmode') === 'decimal')
          .find(e => /price/i.test(labelFor(e)));
      }
      if (!priceEl && attempt < 2) await sleep(400);
    }
    if (!priceEl) return { ok: false, log: [...log, 'PRICE INPUT NOT FOUND'] };
    setInput(priceEl, opts.price); log.push('price -> ' + opts.price);
    await sleep(250);
  }

  // 4) quantity
  const decInputs = Array.from(document.querySelectorAll('input')).filter(e => vis(e) && e.getAttribute('inputmode') === 'decimal');
  const priceIdx = decInputs.findIndex(e => /^price/i.test(labelFor(e)));
  const qtyInput = (opts.price == null || priceIdx < 0) ? decInputs[0] : decInputs[priceIdx + 1];
  if (!qtyInput) return { ok: false, log: [...log, 'QTY INPUT NOT FOUND'] };
  setInput(qtyInput, opts.qty); log.push('qty -> ' + opts.qty);
  await sleep(400);
  const qtyMirror = document.querySelector('[data-name=qtyEl]');
  const mirrorTxt = qtyMirror ? (qtyMirror.innerText || '').trim() : null;
  if (mirrorTxt !== String(opts.qty)) {
    log.push('QTY MIRROR: set ' + opts.qty + ' but qtyEl shows ' + mirrorTxt + ' - retrying');
    await sleep(300);
    setInput(qtyInput, opts.qty);
    await sleep(400);
    const mirrorTxt2 = qtyMirror ? (qtyMirror.innerText || '').trim() : null;
    if (mirrorTxt2 !== String(opts.qty)) return { ok: false, log: [...log, 'QTY MIRROR MISMATCH after retry: ' + mirrorTxt2 + ' - wrong field, aborting'] };
  }
  log.push('qty confirmed via qtyEl mirror = ' + String(opts.qty));

  // 5b) attach protective stop-loss (bracket) if requested
  if (opts.stop != null) {
    let sl = findSection('Stop loss');
    if (!sl) {
      const findExits = () => Array.from(document.querySelectorAll('*')).filter(e => vis(e) && fuzzyEquals(e.innerText, 'exits') && (e.innerText || '').length < 12)[0];
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

  if (String(qtyBack).trim() === '' || Number(qtyBack) <= 0) return { ok: false, log: [...log, 'QTY READBACK INVALID - not opening'] };
  if (opts.price != null && (priceBack == null || String(priceBack).trim() === '')) return { ok: false, log: [...log, 'PRICE READBACK INVALID - not opening'] };

  if (opts.fillOnly) return { ok: true, staged: false, filled: true, chartSymbol, log, priceBack, qtyBack };

  // 6) submit -> opens confirmation (with resilient button finder)
  const btn = await findSubmitButton();
  if (!btn) return { ok: false, chartSymbol, log: [...log, 'SUBMIT BUTTON NOT FOUND (tried place-and-modify-button + fallbacks)'] };
  const btnBefore = (btn.innerText || '').replace(/\\s+/g, ' ').trim();
  // Verify the STAGED order type matches what was requested. The submit button
  // text is TradingView's own rendering of the order (e.g. "Buy 1 MP @ 62 STOP"),
  // so it is ground truth — if the type tab didn't take, abort BEFORE opening the
  // confirmation rather than stage a wrong-type order.
  const _bt = btnBefore.toLowerCase();
  const _typeOk =
    (opts.type === 'market')     ? (/\\bmarket\\b|\\bmkt\\b/.test(_bt) || !/\\blimit\\b|\\bstop\\b/.test(_bt)) :
    (opts.type === 'limit')      ? (/\\blimit\\b|\\blmt\\b/.test(_bt) && !/\\bstop\\b/.test(_bt)) :
    (opts.type === 'stop limit') ? (/stop[\\s-]?limit|stplmt/.test(_bt)) :
    (opts.type === 'stop')       ? (/\\bstop\\b|\\bstp\\b/.test(_bt) && !/stop[\\s-]?limit/.test(_bt)) : true;
  if (!_typeOk) return { ok: false, chartSymbol, log: [...log, 'ORDER-TYPE MISMATCH: requested "' + opts.type + '" but ticket shows "' + btnBefore + '" - NOT opening'] };
  log.push('order-type verified via submit text: "' + btnBefore + '"');
  btn.click(); log.push('clicked submit (was: "' + btnBefore + '")');

  // 7) poll up to ~4s for the confirmation (Send Order / Cancel) to render.
  let confirmVisible = false, rejection = null;
  for (let k = 0; k < 10 && !confirmVisible && !rejection; k++) {
    await sleep(400);
    const sb = Array.from(document.querySelectorAll('button,[role=button]')).find(b => /send order|place order|confirm/i.test(b.innerText || ''));
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
