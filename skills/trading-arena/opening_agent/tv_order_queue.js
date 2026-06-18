#!/usr/bin/env node
/**
 * tv_order_queue.js - stage a QUEUE of TradingView orders for rapid manual
 * confirmation. For each order it: switches the chart to that symbol, fills the
 * ticket (entry + optional attached stop-loss), and opens the confirmation -
 * then WAITS until you click Send Order (or Cancel) and the confirmation
 * closes, and IMMEDIATELY stages the next one.
 *
 * It NEVER clicks Send Order / Confirm. Every real order is your click.
 * Built for the 9:32 window: multiple first-bar matches confirmed back-to-back.
 *
 * Usage:
 *   node tv_order_queue.js --port 9225 --orders-file /path/orders.json
 * orders.json = [{ "symbol":"NYSE:F","side":"buy","type":"stop","price":15.50,"qty":1,"stop":14.00 }, ...]
 *   (omit "stop" to stage entry only)
 */
const fs = require("fs");

function arg(name, def) { const i = process.argv.indexOf("--" + name); return i === -1 ? def : process.argv[i + 1]; }
const PORT = arg("port", "9225");
const ORDERS_FILE = arg("orders-file", null);
const PER_ORDER_TIMEOUT_MS = Number(arg("timeout-ms", "100000"));

if (!ORDERS_FILE) { console.error("--orders-file required"); process.exit(1); }
const orders = JSON.parse(fs.readFileSync(ORDERS_FILE, "utf8"));
if (!Array.isArray(orders) || !orders.length) { console.error("orders file is empty"); process.exit(1); }

const sleep = ms => new Promise(r => setTimeout(r, ms));

// DOM routine: switch symbol, fill ticket (+optional stop), open confirmation.
const STAGE_FN = `async (o) => {
  const log = [];
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const vis = el => !!(el && el.offsetParent !== null && el.getClientRects().length > 0);
  function setInput(el, val){ const proto = el.tagName==='TEXTAREA'?HTMLTextAreaElement:HTMLInputElement; const s=Object.getOwnPropertyDescriptor(proto.prototype,'value').set; el.focus(); s.call(el,String(val)); el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); if(el.blur)el.blur(); }
  function labelFor(inp){ let lab=inp.getAttribute('aria-label')||inp.placeholder||''; let n=inp,h=0; while(n&&h<4&&!lab){n=n.parentElement;h++;if(!n)break;const t=Array.from(n.childNodes).filter(c=>c.nodeType===3).map(c=>c.textContent.trim()).filter(Boolean).join(' ');const le=n.querySelector('label,[class*=label],[class*=Label]');lab=((le?(le.innerText||''):'')+' '+t).replace(/\\s+/g,' ').trim();} return lab; }
  function findSection(word){ let c=Array.from(document.querySelectorAll('*')).filter(e=>vis(e)&&(e.innerText||'').trim().toLowerCase().startsWith(word.toLowerCase())&&(e.innerText||'').length<60); c.sort((a,b)=>(a.innerText||'').length-(b.innerText||'').length); let lab=c[0]; if(!lab)return null; let row=lab,h=0; while(row&&h<6){const t=row.querySelector('[class*=switchContainer],input[type=checkbox],[role=switch]');const p=Array.from(row.querySelectorAll('input')).find(i=>i.getAttribute('inputmode')==='decimal'); if(t&&p)return{toggle:t,price:p}; row=row.parentElement;h++;} return null; }

  // Resilient order-type tab finder with fuzzy matching and retries.
  async function findOrderTypeTab(targetType) {
    const norm = targetType.trim().toLowerCase();
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
      for (const b of visBtns) { const txt = (b.innerText || '').trim().toLowerCase(); if (candidates.includes(txt)) return b; }
      for (const b of visBtns) { const txt = (b.innerText || '').trim().toLowerCase(); for (const c of candidates) { if (txt.startsWith(c)) return b; } }
      for (const b of visBtns) { const dn = (b.getAttribute('data-name') || '').toLowerCase(); if (dn.includes(norm) || dn.includes(norm.replace(' ', '-')) || dn.includes(norm.replace(' ', '_'))) return b; }
      for (const b of visBtns) { const al = (b.getAttribute('aria-label') || '').toLowerCase(); for (const c of candidates) { if (al.includes(c)) return b; } }
      if (attempt < 2) await sleep(600);
    }
    return null;
  }

  // Resilient submit button finder with multiple fallback selectors.
  async function findSubmitButton() {
    for (let attempt = 0; attempt < 3; attempt++) {
      let btn = document.querySelector('[data-name=place-and-modify-button]');
      if (btn && vis(btn)) return btn;
      btn = document.querySelector('[data-name*=place-button],[data-name*=submit-button],[data-name*=order-button]');
      if (btn && vis(btn)) return btn;
      const allBtns = Array.from(document.querySelectorAll('button,[role=button]')).filter(b => vis(b));
      btn = allBtns.find(b => { const txt = (b.innerText || '').trim().toLowerCase(); return /^(buy|sell)\\s/.test(txt) && !/send order|cancel/i.test(txt); });
      if (btn) return btn;
      btn = allBtns.find(b => /place|review order|create order/i.test((b.innerText || '').trim()));
      if (btn) return btn;
      if (attempt < 2) await sleep(500);
    }
    return null;
  }

  // Ensure the order ENTRY ticket is open. The Questrade panel shows
  // Positions/Orders but the entry form (order-type tabs/side/place) stays
  // closed until the toolbar "Trade" button is clicked.
  async function ensureTicketOpen() {
    const up = () => !!(document.querySelector('[data-name=place-and-modify-button]') || document.querySelector('[data-name^=side-control]'));
    if (up()) return true;
    for (let attempt = 0; attempt < 3 && !up(); attempt++) {
      const trade = Array.from(document.querySelectorAll('button,[role=button]')).find(b => vis(b) && /^trade$/i.test((b.innerText || '').trim()));
      if (trade) trade.click();
      for (let k = 0; k < 12 && !up(); k++) await sleep(300);
    }
    return up();
  }

  // ── modify-stop: reprice an existing resting stop IN PLACE (no cancel/gap) ──
  if (o.action === 'modify-stop' || o.action === 'modify-tp') {
    const otab = Array.from(document.querySelectorAll('button,[role=button],[role=tab]')).find(b=>vis(b)&&/^orders\\b/i.test((b.innerText||'').trim()));
    if (otab) { otab.click(); await sleep(700); }
    const t = document.querySelector('[data-name=\\"QUESTRADE.orders-table\\"]');
    if (!t) return {ok:false, log:['no orders table']};
    const row = Array.from(t.querySelectorAll('[role=row],tr')).filter(vis).find(r=>{
      const txt=(r.innerText||'').replace(/\\s+/g,' ').trim().toUpperCase();
      return txt.startsWith(String(o.symbol).toUpperCase()+' ') && /\\bSTOP\\b/.test(txt) && /QUEUED/.test(txt);
    });
    if (!row) return {ok:false, log:['no resting QUEUED bracket order for '+o.symbol]};
    const edit = row.querySelector('[data-name=edit-settings-cell-button]');
    if (!edit) return {ok:false, log:['no Modify button on the '+o.symbol+' row']};
    edit.click(); await sleep(1200);
    const setWhat = [];
    if (o.price != null) {
      const pe = Array.from(document.querySelectorAll('input')).filter(e=>vis(e)&&e.type==='text').find(e=>/^price/i.test(labelFor(e)));
      if (!pe) return {ok:false, log:['no Price field in Modify dialog']};
      setInput(pe, o.price); setWhat.push('stop '+o.price); await sleep(300);
    }
    if (o.take_profit != null) {
      let tp = findSection('Take profit');
      if (!tp) { const fe=()=>Array.from(document.querySelectorAll('*')).filter(e=>vis(e)&&(e.innerText||'').trim().toLowerCase()==='exits'&&(e.innerText||'').length<12)[0]; for(let a=0;a<2&&!tp;a++){const hdr=fe(); if(!hdr)break; (hdr.closest('[role=button]')||hdr.parentElement||hdr).click(); await sleep(650); tp=findSection('Take profit');} }
      if (!tp) return {ok:false, log:['no Take profit section in Modify dialog']};
      const cb = tp.toggle.querySelector('input[type=checkbox]') || (tp.toggle.tagName==='INPUT'?tp.toggle:null);
      if (!cb) return {ok:false, log:['no Take profit checkbox']};
      if (!cb.checked) { cb.click(); await sleep(400); }
      if (!cb.checked) return {ok:false, log:['Take profit toggle would not enable']};
      setInput(tp.price, o.take_profit); setWhat.push('TP '+o.take_profit); await sleep(300);
    }
    if (!setWhat.length) return {ok:false, log:['modify called with neither stop nor take_profit']};
    let cf = null, enabled = false;
    for (let k=0; k<15 && !enabled; k++) { cf = document.querySelector('[data-name=place-and-modify-button]'); enabled = !!(cf && vis(cf) && !cf.disabled && !/disabled/i.test(cf.className||'')); if(!enabled) await sleep(400); }
    if (!cf || !vis(cf)) return {ok:false, log:['Modify Confirm button not visible']};
    return {ok:true, staged:true, summary:'Modify '+o.symbol+' '+setWhat.join(', ')+(enabled?' (Confirm ready)':' (wait for Confirm to enable)'), log:['modify '+o.symbol+': '+setWhat.join(', ')+', confirm-enabled='+enabled]};
  }

  // 1) switch symbol + verify
  try { window.TradingViewApi._activeChartWidgetWV.value().setSymbol(o.symbol, {}); } catch(e){ return {ok:false, log:['setSymbol failed: '+e.message]}; }
  await sleep(1800);
  const chartSymbol = window.TradingViewApi._activeChartWidgetWV.value().symbol();
  if (String(chartSymbol).toUpperCase().indexOf(String(o.symbol).toUpperCase().split(':').pop()) === -1)
    return { ok:false, chartSymbol, log:['SYMBOL MISMATCH: wanted '+o.symbol+' got '+chartSymbol] };
  log.push('symbol -> ' + chartSymbol);

  // marketable-limit (type 'close'): read the live price off the side button and
  // cross the spread, so it fills immediately like a market order but satisfies
  // securities that reject MARKET (e.g. OTC names require a limit).
  let otype = o.type, oprice = (o.price == null ? null : o.price);
  if (o.type === 'close' || o.marketable) {
    const frac = Math.abs(o.qty - Math.round(o.qty)) > 1e-9;
    if (frac) {
      otype = 'market'; oprice = null;
      log.push('fractional qty ' + o.qty + ' -> MARKET close (limit would be rejected)');
    } else {
      const pb = document.querySelector('[data-name='+o.side+'-order-button]');
      const mm = pb ? (pb.innerText||'').match(/[0-9][0-9.,]*/) : null;
      const px = mm ? parseFloat(mm[0].replace(/,/g,'')) : null;
      if (!px) return {ok:false,submitted:false,chartSymbol,log:[...log,'could not read live price for marketable close']};
      oprice = (o.side==='sell') ? +(px*0.99).toFixed(2) : +(px*1.01).toFixed(2);
      otype = 'limit';
      log.push('marketable -> limit @ '+oprice+' (live '+px+')');
    }
  }

  // 1b) make sure the entry ticket is open before locating its controls
  if (!(await ensureTicketOpen())) return {ok:false,chartSymbol,log:[...log,'ORDER TICKET NOT OPEN: clicked "Trade" but the entry form never appeared']};

  // 2) order type (resilient finder with retries)
  const tab = await findOrderTypeTab(otype);
  if(!tab) {
    const allBtns = Array.from(document.querySelectorAll('button,[role=button],[role=tab]')).filter(b=>vis(b)).map(b=>(b.innerText||'').trim().toLowerCase().slice(0,30));
    return {ok:false,chartSymbol,log:[...log,'ORDER-TYPE TAB NOT FOUND: '+otype+' | visible: '+JSON.stringify(allBtns.slice(0,15))]};
  }
  tab.click(); log.push('type -> '+otype); await sleep(450);

  // 3) side
  let sideEl = document.querySelector('[data-name=side-control-'+o.side+']');
  if (!sideEl) {
    await sleep(300);
    sideEl = Array.from(document.querySelectorAll('button,[role=button],[role=tab]')).find(b => vis(b) && (b.getAttribute('data-name')||'').includes('side') && (b.innerText||'').trim().toLowerCase() === o.side);
  }
  if(!sideEl) return {ok:false,chartSymbol,log:[...log,'SIDE NOT FOUND']};
  sideEl.click(); log.push('side -> '+o.side); await sleep(350);

  // 4) price
  if(oprice!=null){
    let pe = null;
    for (let attempt = 0; attempt < 3 && !pe; attempt++) {
      pe = Array.from(document.querySelectorAll('input')).filter(e=>vis(e)&&e.type==='text').find(e=>/^price/i.test(labelFor(e)));
      if (!pe) pe = Array.from(document.querySelectorAll('input')).filter(e=>vis(e)&&e.getAttribute('inputmode')==='decimal').find(e=>/price/i.test(labelFor(e)));
      if (!pe && attempt < 2) await sleep(400);
    }
    if(!pe) return {ok:false,chartSymbol,log:[...log,'PRICE INPUT NOT FOUND']};
    setInput(pe,oprice); log.push('price -> '+oprice); await sleep(250);
  }

  // 5) qty (decimal input after Price; verify via qtyEl mirror)
  const dec=Array.from(document.querySelectorAll('input')).filter(e=>vis(e)&&e.getAttribute('inputmode')==='decimal');
  const pIdx=dec.findIndex(e=>/^price/i.test(labelFor(e)));
  const qEl=(oprice==null||pIdx<0)?dec[0]:dec[pIdx+1];
  if(!qEl) return {ok:false,chartSymbol,log:[...log,'QTY INPUT NOT FOUND']};
  setInput(qEl,o.qty); await sleep(400);
  const mirror=document.querySelector('[data-name=qtyEl]'); const mtxt=mirror?(mirror.innerText||'').trim():null;
  if(mtxt!==String(o.qty)){
    log.push('QTY MIRROR: set '+o.qty+' but qtyEl shows '+mtxt+' - retrying');
    await sleep(300); setInput(qEl,o.qty); await sleep(400);
    const mtxt2=mirror?(mirror.innerText||'').trim():null;
    if(mtxt2!==String(o.qty)) return {ok:false,chartSymbol,log:[...log,'QTY MIRROR MISMATCH after retry: '+mtxt2]};
  }
  log.push('qty -> '+o.qty+' (confirmed)');

  // 5b) attach protective stop-loss (expand Exits if needed; click inner checkbox)
  if(o.stop!=null){
    let sl=findSection('Stop loss');
    if(!sl){ const fe=()=>Array.from(document.querySelectorAll('*')).filter(e=>vis(e)&&(e.innerText||'').trim().toLowerCase()==='exits'&&(e.innerText||'').length<12)[0]; for(let a=0;a<2&&!sl;a++){const hdr=fe(); if(!hdr)break; (hdr.closest('[role=button]')||hdr.parentElement||hdr).click(); await sleep(650); sl=findSection('Stop loss');} }
    if(!sl) return {ok:false,chartSymbol,log:[...log,'STOP-LOSS SECTION NOT FOUND']};
    const cb=sl.toggle.querySelector('input[type=checkbox]')||(sl.toggle.tagName==='INPUT'?sl.toggle:null);
    if(!cb) return {ok:false,chartSymbol,log:[...log,'STOP-LOSS CHECKBOX NOT FOUND']};
    if(!cb.checked){cb.click(); await sleep(400);}
    if(!cb.checked) return {ok:false,chartSymbol,log:[...log,'STOP-LOSS TOGGLE would not enable']};
    setInput(sl.price,o.stop); await sleep(300);
    log.push('stop-loss -> '+o.stop+' (on='+cb.checked+')');
  }

  // 6) submit -> open confirmation (resilient button finder + rejection detection)
  const submit = await findSubmitButton();
  if(!submit) return {ok:false,chartSymbol,log:[...log,'SUBMIT BUTTON NOT FOUND (tried place-and-modify-button + fallbacks)']};
  const before=(submit.innerText||'').replace(/\\s+/g,' ').trim();
  submit.click(); log.push('clicked submit (was "'+before+'")');

  let confirmVisible=false, rejection=null;
  for(let k=0;k<25&&!confirmVisible&&!rejection;k++){
    await sleep(400);
    const sb=Array.from(document.querySelectorAll('button,[role=button]')).find(b=>vis(b)&&/send order|place order|confirm/i.test(b.innerText||'')); confirmVisible=!!sb;
    const rj=Array.from(document.querySelectorAll('*')).find(e=>vis(e)&&/was rejected|both marketable|order rejected|please modify|insufficient|not allowed/i.test(e.innerText||'')&&(e.innerText||'').length<220); if(rj) rejection=(rj.innerText||'').replace(/\\s+/g,' ').trim().slice(0,180);
  }
  if(rejection) return {ok:false,submitted:true,rejection,chartSymbol,log:[...log,'ORDER REJECTED: '+rejection]};
  if(!confirmVisible){ const after=(document.querySelector('[data-name=place-and-modify-button]')||{}).innerText||''; return {ok:false,submitted:true,chartSymbol,summary:before,log:[...log,'confirm dialog not detected (will reconcile via positions); button now "'+after.replace(/\\s+/g,' ').trim()+'"']}; }
  return { ok:true, submitted:true, staged:true, chartSymbol, summary:before, log };
}`;

const CONFIRM_VISIBLE_FN = `(function(){ var b=Array.from(document.querySelectorAll('button,[role=button]')).find(x=>(x.offsetParent!==null && x.getClientRects().length>0) && /send order|place order|confirm/i.test(x.innerText||'')); return !!b; })()`;

const READ_POSITIONS_FN = `(async()=>{
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const vis=el=>!!(el&&el.offsetParent!==null&&el.getClientRects().length>0);
  const tab=Array.from(document.querySelectorAll('button,[role=button],[role=tab]')).find(b=>vis(b)&&/^positions\\b/i.test((b.innerText||'').trim()));
  if(tab){tab.click(); await sleep(900);}
  const t=document.querySelector('[data-name=\\"QUESTRADE.positions-table\\"]');
  if(!t) return JSON.stringify([]);
  const out=[];
  for(const r of Array.from(t.querySelectorAll('[role=row],tr')).filter(vis)){
    const toks=(r.innerText||'').replace(/\\s+/g,' ').trim().split(' ');
    if(toks.length<3) continue;
    const side=toks[1]; if(!/^(long|short)$/i.test(side)) continue;
    const qty=parseFloat(String(toks[2]).replace(/,/g,'')); if(!(qty>0)) continue;
    out.push({symbol:toks[0], side:side.toLowerCase(), qty});
  }
  return JSON.stringify(out);
})()`;

(async () => {
  const tabs = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
  const tv = require("./tv_tab").pickTradingTab(tabs);   // never the dedicated data tab
  if (!tv) { console.error("no chart tab"); process.exit(2); }
  const ws = tv.webSocketDebuggerUrl.replace(/\/\/[^/]+\//, `//127.0.0.1:${PORT}/`);
  const sock = new WebSocket(ws);
  let id = 0; const w = {};
  sock.addEventListener("message", e => { const m = JSON.parse(e.data); if (m.id && w[m.id]) { w[m.id](m); delete w[m.id]; } });
  const call = (method, params = {}) => new Promise(res => { const i = ++id; w[i] = res; sock.send(JSON.stringify({ id: i, method, params })); });
  const evalJs = async (expr, awaitP = false) => {
    const r = await call("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: awaitP });
    if (r.result && r.result.exceptionDetails) throw new Error("page exception: " + JSON.stringify(r.result.exceptionDetails).slice(0, 200));
    return r.result && r.result.result ? r.result.result.value : undefined;
  };
  await new Promise(res => sock.addEventListener("open", res));

  const isClose = o => (o.type === 'close' || o.marketable);
  const ticker = s => String(s).split(':').pop().toUpperCase();
  const closeOrders = orders.filter(isClose);

  const posBefore = {};
  if (closeOrders.length) {
    try {
      JSON.parse(await evalJs(READ_POSITIONS_FN, true) || "[]").forEach(p => { posBefore[ticker(p.symbol)] = p.qty; });
      console.log("positions before:", JSON.stringify(posBefore));
    } catch (e) { console.log("could not read positions before:", e.message); }
  }

  const results = [];
  const NO_DIALOG_FALLBACK_MS = 12000;
  for (let i = 0; i < orders.length; i++) {
    const o = orders[i];
    const desc = o.action === 'modify-stop' ? `modify ${o.symbol} stop -> ${o.price}`
      : o.action === 'modify-tp' ? `modify ${o.symbol} take-profit -> ${o.take_profit}`
      : `${o.side} ${o.qty} ${o.symbol} ${o.type} @ ${o.price}${o.stop != null ? ' (SL ' + o.stop + ')' : ''}`;
    console.log(`\n=== [${i + 1}/${orders.length}] staging ${desc} ===`);
    const staged = await evalJs(`(${STAGE_FN})(${JSON.stringify(o)})`, true);
    staged.log.forEach(l => console.log("   " + l));
    if (staged.rejection) { console.log("   !! broker REJECTED - skipping"); results.push({ ...o, staged: false, rejected: staged.rejection }); continue; }
    if (!staged.submitted) { console.log("   !! could not fill ticket - skipping to next"); results.push({ ...o, staged: false }); continue; }
    console.log(`   >> CONFIRM ON SCREEN${staged.ok ? '' : ' (dialog not auto-detected)'}: ${staged.summary || desc}  (click Send Order, or Cancel to skip)`);

    const start = Date.now();
    let sawOpen = staged.ok, closed = false;
    await sleep(300);
    while (Date.now() - start < PER_ORDER_TIMEOUT_MS) {
      const open = await evalJs(CONFIRM_VISIBLE_FN);
      if (open) sawOpen = true;
      else if (sawOpen) { closed = true; break; }
      else if (Date.now() - start > NO_DIALOG_FALLBACK_MS) break;
      await sleep(350);
    }
    if (sawOpen && !closed) { console.log("   .. timed out waiting for your confirm - stopping queue"); results.push({ ...o, staged: true, dialogClosed: false }); break; }
    console.log(sawOpen ? "   .. confirmation closed - advancing"
      : isClose(o) ? "   .. advancing (outcome will be reconciled via positions)"
      : "   .. advancing (confirm not auto-detected)");
    results.push({ ...o, staged: true, dialogClosed: closed, sawConfirm: sawOpen });
    await sleep(400);
  }

  let reconcile = null;
  if (closeOrders.length) {
    const posAfter = {};
    try {
      JSON.parse(await evalJs(READ_POSITIONS_FN, true) || "[]").forEach(p => { posAfter[ticker(p.symbol)] = p.qty; });
    } catch (e) { console.log("could not read positions after:", e.message); }
    const rows = closeOrders.map(o => {
      const t = ticker(o.symbol);
      const before = posBefore[t] || 0, after = posAfter[t] || 0;
      const flattened = before > 0 ? (after <= before - Math.min(o.qty, before) + 1e-6) : (after === 0);
      return { symbol: t, before, after, ordered: o.qty, flattened };
    });
    const done = rows.filter(r => r.flattened).length;
    console.log("\nRECONCILED CLOSES (from Questrade positions):");
    rows.forEach(r => console.log(`   ${r.symbol}: ${r.before} -> ${r.after}  ${r.flattened ? '✓ flattened' : '✗ STILL HELD'}`));
    console.log(`   => ${done}/${closeOrders.length} flattened`);
    reconcile = { reconciledAt: new Date().toISOString(), flattened: done, total: closeOrders.length, rows };
    try {
      const p = require("path").join(__dirname, "..", "logs", "opening_close_reconcile.json");
      fs.writeFileSync(p, JSON.stringify(reconcile));
    } catch (e) { console.log("could not write reconcile file:", e.message); }
  }

  sock.close();
  console.log(`\nqueue done: staged ${results.filter(r => r.staged).length}/${orders.length}`
    + (reconcile ? `, closes flattened ${reconcile.flattened}/${reconcile.total} (per positions)` : ""));
  process.exit(0);
})().catch(e => { console.error("ERR", e.message); process.exit(3); });
