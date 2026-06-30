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
// --dry: run the FULL staging path (incl. stop-loss attach + verification) but
// NEVER click submit, and neutralize the ticket afterward. For pre-open testing.
const DRY = process.argv.includes("--dry");

if (!ORDERS_FILE) { console.error("--orders-file required"); process.exit(1); }
const orders = JSON.parse(fs.readFileSync(ORDERS_FILE, "utf8"));
if (!Array.isArray(orders) || !orders.length) { console.error("orders file is empty"); process.exit(1); }

const sleep = ms => new Promise(r => setTimeout(r, ms));

// DOM routine: switch symbol, fill ticket (+optional stop), open confirmation.
const STAGE_FN = `async (o, dry) => {
  const log = [];
  // HARD GUARD: never operate on a DATA tab. An orphaned data tab (our nonce in
  // window, tracking file lost) can be mis-picked as the trading tab; staging or
  // setSymbol there never reaches the broker. The trading tab never has this.
  if (window.__OPENING_DATA_NONCE__) {
    return { ok: false, log: ['REFUSING: connected to a DATA tab (has __OPENING_DATA_NONCE__), not the trading tab. Run tv_data_teardown.js to close orphaned data tabs.'] };
  }
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const vis = el => !!(el && el.offsetParent !== null && el.getClientRects().length > 0);
  // Char-drop-tolerant matching: Windows Chrome intermittently drops chars from
  // innerText via CDP (e.g. "Stop loss"->"Stop lo"). Checks if text's chars are
  // a subsequence of target (dropped chars = skipped in target).
  const _isSubseq = (short, long) => { let li=0; for(let si=0;si<short.length;si++){while(li<long.length&&long[li]!==short[si])li++;if(li>=long.length)return false;li++;} return true; };
  const fuzzyStartsWith = (text, target) => { const t=(text||'').trim().toLowerCase(),g=target.toLowerCase(); if(t.startsWith(g))return true; const prefix=t.slice(0,Math.ceil(g.length*1.5)); return prefix.length>=g.length*0.5&&_isSubseq(prefix,g); };
  const fuzzyEquals = (text, target) => { const t=(text||'').trim().toLowerCase(),g=target.toLowerCase(); if(t===g)return true; return t.length>=g.length*0.5&&t.length<=g.length*1.5&&_isSubseq(t,g); };
  function setInput(el, val){ const proto = el.tagName==='TEXTAREA'?HTMLTextAreaElement:HTMLInputElement; const s=Object.getOwnPropertyDescriptor(proto.prototype,'value').set; el.focus(); s.call(el,String(val)); el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); if(el.blur)el.blur(); }
  function labelFor(inp){ let lab=inp.getAttribute('aria-label')||inp.placeholder||''; let n=inp,h=0; while(n&&h<4&&!lab){n=n.parentElement;h++;if(!n)break;const t=Array.from(n.childNodes).filter(c=>c.nodeType===3).map(c=>c.textContent.trim()).filter(Boolean).join(' ');const le=n.querySelector('label,[class*=label],[class*=Label]');lab=((le?(le.innerText||''):'')+' '+t).replace(/\\s+/g,' ').trim();} return lab; }
  function findSection(word){ const stx=e=>(e.textContent||e.innerText||''); let c=Array.from(document.querySelectorAll('*')).filter(e=>vis(e)&&fuzzyStartsWith(stx(e),word)&&stx(e).length<60); c.sort((a,b)=>stx(a).length-stx(b).length); let lab=c[0]; if(!lab)return null; let row=lab,h=0; while(row&&h<6){const t=row.querySelector('[class*=switchContainer],input[type=checkbox],[role=switch]');const p=Array.from(row.querySelectorAll('input')).find(i=>i.getAttribute('inputmode')==='decimal'); if(t&&p)return{toggle:t,price:p}; row=row.parentElement;h++;} return null; }
  // Robust, VERIFIED stop-loss attach. Returns {ok, reason, readback, log:[]}.
  // Anchors on the SL TOGGLE (a role=switch / checkbox whose ancestor text says
  // "stop loss") — the old shortest-"Stop loss"-label walk was the bug (TV's DOM
  // puts the price input in a cousin grid, not an ancestor row). Forces the toggle
  // ON, locates the SL price input by DOCUMENT ORDER after the switch, types the
  // ABSOLUTE price, then VERIFIES three ways so a ticks-mode field, a stale value,
  // or a wrong-side stop can never pass: readback≈want, stop on the correct side
  // of entry, and within a sane band of entry.
  async function attachStopLoss(stopPx, side, entryPx){
    const lg=[];
    // Pick the SL switch by its CLOSEST labeled ancestor — the Take-profit switch
    // shares an outer container whose text also contains "stop loss", so a naive
    // ancestor-contains match grabs TP and silently sets the wrong field. Decide at
    // the FIRST ancestor that mentions either exit, requiring stop-loss to win.
    const slSwitch=()=>Array.from(document.querySelectorAll('[role=switch],input[type=checkbox]')).filter(vis)
      .find(s=>{let p=s,h=0;while(p&&h<6){const t=(p.textContent||'').toLowerCase();const mSL=t.search(/stop\\s*loss/),mTP=t.search(/take\\s*profit/);if(mSL>=0||mTP>=0)return mSL>=0&&(mTP<0||mSL<mTP);p=p.parentElement;h++;}return false;});
    let sw=slSwitch();
    if(!sw){ // section may be collapsed — expand "Exits" once, then retry
      const ex=Array.from(document.querySelectorAll('*')).filter(e=>vis(e)&&fuzzyEquals(e.innerText,'exits')&&(e.innerText||'').length<12)[0];
      if(ex){ (ex.closest('[role=button]')||ex.parentElement||ex).click(); await sleep(650); sw=slSwitch(); }
    }
    if(!sw) return {ok:false,reason:'SL switch not found',log:lg};
    lg.push('SL switch found (checked='+sw.checked+')');
    if(sw.checked!==true){ sw.click(); await sleep(450); sw=slSwitch(); }
    if(!sw||sw.checked!==true) return {ok:false,reason:'SL toggle would not enable',log:lg};
    const decs=Array.from(document.querySelectorAll('input')).filter(e=>vis(e)&&e.getAttribute('inputmode')==='decimal');
    const slInput=decs.find(d=>(sw.compareDocumentPosition(d)&4)); // 4 = DOCUMENT_POSITION_FOLLOWING
    if(!slInput) return {ok:false,reason:'SL price input not found after toggle',log:lg};
    setInput(slInput,stopPx); await sleep(350);
    const rb=parseFloat(String(slInput.value).replace(/,/g,''));
    lg.push('SL input set '+stopPx+' -> readback "'+slInput.value+'"');
    if(!isFinite(rb)) return {ok:false,reason:'readback not numeric "'+slInput.value+'"',log:lg};
    if(Math.abs(rb-stopPx)/stopPx>0.01) return {ok:false,reason:'readback mismatch got '+rb+' want '+stopPx,log:lg};
    if(entryPx!=null&&isFinite(entryPx)){
      if(side==='buy'&&rb>=entryPx) return {ok:false,reason:'BUY stop '+rb+' not below entry '+entryPx,log:lg};
      if(side==='sell'&&rb<=entryPx) return {ok:false,reason:'SELL stop '+rb+' not above entry '+entryPx,log:lg};
      if(Math.abs(rb-entryPx)/entryPx>0.25) return {ok:false,reason:'stop '+rb+' >25% from entry '+entryPx+' (unit/scale error?)',log:lg};
    }
    return {ok:true,readback:rb,log:lg};
  }

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
    // textContent (raw DOM text), NOT innerText: the Windows Chrome intermittently
    // returns EMPTY innerText for a freshly-rendered ticket over CDP, while
    // textContent is layout-independent and stays populated. The order-type tabs
    // carry no data-name/aria-label (verified 2026-06-22), so text is the only key.
    const tx = b => ((b.textContent || b.innerText || '').trim().toLowerCase());
    for (let attempt = 0; attempt < 6; attempt++) {
      const allBtns = Array.from(document.querySelectorAll('button,[role=button],[role=tab],[data-name*=order-type]'));
      const visBtns = allBtns.filter(b => vis(b));
      for (const b of visBtns) { if (candidates.includes(tx(b))) return b; }
      for (const b of visBtns) { const t = tx(b); for (const c of candidates) { if (t.startsWith(c)) return b; } }
      for (const b of visBtns) { const dn = (b.getAttribute('data-name') || '').toLowerCase(); if (dn.includes(norm) || dn.includes(norm.replace(' ', '-')) || dn.includes(norm.replace(' ', '_'))) return b; }
      for (const b of visBtns) { const al = (b.getAttribute('aria-label') || '').toLowerCase(); for (const c of candidates) { if (al.includes(c)) return b; } }
      if (attempt < 5) await sleep(600);
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
      const trade = Array.from(document.querySelectorAll('button,[role=button]')).find(b => vis(b) && fuzzyEquals(b.innerText, 'trade'));
      if (trade) trade.click();
      for (let k = 0; k < 12 && !up(); k++) await sleep(300);
    }
    return up();
  }

  // ── modify-stop: reprice an existing resting stop IN PLACE (no cancel/gap) ──
  if (o.action === 'modify-stop' || o.action === 'modify-tp') {
    const otab = Array.from(document.querySelectorAll('button,[role=button],[role=tab]')).find(b=>vis(b)&&fuzzyEquals(b.innerText,'orders'));
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
      if (!tp) { const fe=()=>Array.from(document.querySelectorAll('*')).filter(e=>vis(e)&&fuzzyEquals(e.innerText,'exits')&&(e.innerText||'').length<12)[0]; for(let a=0;a<2&&!tp;a++){const hdr=fe(); if(!hdr)break; (hdr.closest('[role=button]')||hdr.parentElement||hdr).click(); await sleep(650); tp=findSection('Take profit');} }
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

  // 5b) attach protective stop-loss — VERIFIED. On ANY failure, NEUTRALIZE the
  // ticket (blank the qty so the entry is not sendable) and abort. This is the
  // no-naked-long guarantee: a stopless entry can never be left on screen to send.
  if(o.stop!=null){
    const sl=await attachStopLoss(o.stop,o.side,oprice);
    sl.log.forEach(l=>log.push(l));
    if(!sl.ok){
      try{ setInput(qEl,'0'); }catch(e){}
      return {ok:false,nakedGuard:true,reason:sl.reason,chartSymbol,log:[...log,'STOP-LOSS ATTACH FAILED ('+sl.reason+') — ticket NEUTRALIZED (qty->0); entry NOT sendable']};
    }
    log.push('stop-loss -> '+o.stop+' (VERIFIED readback='+sl.readback+', side='+o.side+' vs entry '+oprice+')');
  }

  // DRY-RUN: full path exercised (incl. verified stop-loss) but NEVER submit.
  // Neutralize the ticket so nothing is left sendable, then report success.
  if(dry){
    try{ setInput(qEl,'0'); }catch(e){}
    return {ok:true,dry:true,chartSymbol,summary:(o.side||'')+' '+o.qty+' '+o.symbol+' @ '+oprice+(o.stop!=null?(' SL '+o.stop):'')+' [verified, not sent]',log:[...log,'DRY-RUN: stop verified, ticket neutralized, NOT submitted']};
  }

  // 6) submit -> open confirmation (resilient button finder + rejection detection)
  const submit = await findSubmitButton();
  if(!submit) return {ok:false,chartSymbol,log:[...log,'SUBMIT BUTTON NOT FOUND (tried place-and-modify-button + fallbacks)']};
  const before=(submit.innerText||'').replace(/\\s+/g,' ').trim();
  // Verify staged type matches otype (the actual type after close/marketable
  // remapping) via the submit button's own text - abort if the tab didn't take.
  const _bt=before.toLowerCase();
  const _typeOk =
    (otype==='market')     ? (/\\bmarket\\b|\\bmkt\\b/.test(_bt) || !/\\blimit\\b|\\bstop\\b/.test(_bt)) :
    (otype==='limit')      ? (/\\blimit\\b|\\blmt\\b/.test(_bt) && !/\\bstop\\b/.test(_bt)) :
    (otype==='stop limit') ? (/stop[\\s-]?limit|stplmt/.test(_bt)) :
    (otype==='stop')       ? (/\\bstop\\b|\\bstp\\b/.test(_bt) && !/stop[\\s-]?limit/.test(_bt)) : true;
  if(!_typeOk) return {ok:false,chartSymbol,log:[...log,'ORDER-TYPE MISMATCH: requested "'+otype+'" but ticket shows "'+before+'" - NOT opening']};
  log.push('order-type verified via submit text: "'+before+'"');
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

const READ_ORDERS_FN = `(async()=>{
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const vis=el=>!!(el&&el.offsetParent!==null&&el.getClientRects().length>0);
  const tab=Array.from(document.querySelectorAll('button,[role=button],[role=tab]')).find(b=>vis(b)&&/^orders\\b/i.test((b.innerText||'').trim()));
  if(tab){tab.click(); await sleep(800);}
  const t=document.querySelector('[data-name=\\"QUESTRADE.orders-table\\"]');
  if(!t) return JSON.stringify([]);
  const out=[];
  for(const r of Array.from(t.querySelectorAll('[role=row],tr')).filter(vis)){
    const txt=(r.innerText||'').replace(/\\s+/g,' ').trim();
    if(!txt) continue;
    const sym=txt.split(' ')[0];
    const live=/\\b(QUEUED|WORKING|PENDING|PARTIAL)\\b/i.test(txt) && !/\\b(CANCEL|REJECT)/i.test(txt);
    out.push({symbol:sym, live, text:txt.slice(0,90)});
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
  // Real-time backup: if CDP staging can't fill the ticket (cold-DOM miss / TV
  // change), don't lose the trade silently — Telegram the EXACT manual order so
  // the operator can place it by hand in seconds.
  const manualMsg = o =>
      o.action === 'modify-stop' ? `⚠️ STOP-MOVE failed to stage — move ${o.symbol} stop to ${o.price} BY HAND now.`
    : o.action === 'modify-tp'   ? `⚠️ TAKE-PROFIT failed to stage — set ${o.symbol} TP limit ${o.take_profit} BY HAND now.`
    : (o.type === 'close' || o.marketable) ? `⚠️ CLOSE failed to stage — SELL ${o.qty} ${o.symbol} at MARKET BY HAND now.`
    : `⚠️ STAGE FAILED — place BY HAND now: ${(o.side||'buy').toUpperCase()} ${o.qty} ${o.symbol} ${(o.type||'stop').toUpperCase()} @ ${o.price}${o.stop != null ? `, STOP-LOSS ${o.stop}` : ''}.`;
  async function tg(text) {
    const tok = process.env.TELEGRAM_STOCK_BOT_TOKEN, chat = process.env.TELEGRAM_CHAT_ID || "6545739863";
    if (!tok) { console.log("   (no TELEGRAM_STOCK_BOT_TOKEN — manual alert not sent)"); return; }
    try { await fetch(`https://api.telegram.org/bot${tok}/sendMessage`, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: new URLSearchParams({ chat_id: chat, text }) }); }
    catch (e) { console.log("   (manual alert send failed: " + e.message + ")"); }
  }
  for (let i = 0; i < orders.length; i++) {
    const o = orders[i];
    const desc = o.action === 'modify-stop' ? `modify ${o.symbol} stop -> ${o.price}`
      : o.action === 'modify-tp' ? `modify ${o.symbol} take-profit -> ${o.take_profit}`
      : `${o.side} ${o.qty} ${o.symbol} ${o.type} @ ${o.price}${o.stop != null ? ' (SL ' + o.stop + ')' : ''}`;
    console.log(`\n=== [${i + 1}/${orders.length}] staging ${desc}${DRY ? ' [DRY]' : ''} ===`);
    let staged = await evalJs(`(${STAGE_FN})(${JSON.stringify(o)}, ${DRY})`, true);
    staged.log.forEach(l => console.log("   " + l));
    if (DRY) {
      const okv = staged.ok === true;
      console.log(`   [DRY] ${okv ? 'PASS — stop verified & ticket neutralized' : 'FAIL — ' + (staged.reason || 'see log above')}`);
      results.push({ ...o, dry: true, ok: okv, reason: staged.reason || null });
      continue;
    }
    if (staged.rejection) { console.log("   !! broker REJECTED - skipping"); await tg(`⛔ ${o.symbol} broker REJECTED: ${staged.rejection}. Review by hand.`); results.push({ ...o, staged: false, rejected: staged.rejection }); continue; }
    if (!staged.submitted) {
      console.log("   .. stage failed — re-warming and retrying once");
      await sleep(800);
      const retry = await evalJs(`(${STAGE_FN})(${JSON.stringify(o)})`, true);
      retry.log.forEach(l => console.log("   [retry] " + l));
      if (retry.submitted) { staged = retry; }
      else {
        console.log("   !! could not fill ticket after retry — sending manual-fallback alert");
        await tg(manualMsg(o));
        results.push({ ...o, staged: false, fallbackAlerted: true });
        continue;
      }
    }
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

  // ── entry verification (fill-truth): the confirm dialog CLOSING does not prove the
  // order was SENT — clicking Cancel or dismissing it closes the dialog too, and the
  // queue would still log "confirmation closed - advancing". After the batch, read the
  // broker ONCE and confirm each staged ENTRY is actually resting (QUEUED) or already
  // filled (a position). Anything missing was a missed/cancelled Send Order; alert so
  // the bid isn't lost silently (the SMR/GILT 2026-06-29 failure). Non-fatal.
  let verify = null;
  const entryOrders = orders.filter(o => !o.action && !isClose(o));
  if (!DRY && entryOrders.length) {
    await sleep(1200);   // let the last send settle into the orders/positions table
    let ords = [], pos = [];
    try { ords = JSON.parse(await evalJs(READ_ORDERS_FN, true) || "[]"); } catch (e) { console.log("could not read orders for verify:", e.message); }
    try { pos  = JSON.parse(await evalJs(READ_POSITIONS_FN, true) || "[]"); } catch (e) { console.log("could not read positions for verify:", e.message); }
    const liveT = new Set(ords.filter(r => r.live).map(r => ticker(r.symbol)));
    const heldT = new Set(pos.filter(p => (p.qty || 0) > 0).map(p => ticker(p.symbol)));
    console.log("\nENTRY VERIFICATION (must be QUEUED at broker or already filled):");
    const unsent = [];
    const rows = entryOrders.map(o => {
      const t = ticker(o.symbol);
      const live = liveT.has(t), held = heldT.has(t), ok = live || held;
      console.log(`   ${t}: ${ok ? (held ? '✓ FILLED (position)' : '✓ resting (queued)') : '✗ NOT AT BROKER — was not sent'}`);
      const res = results.find(r => r.symbol === o.symbol && r.staged);   // only ones the queue believed it staged
      if (res) res.verified = ok;
      if (!ok && res) unsent.push(o);
      return { symbol: t, live, held, ok };
    });
    for (const o of unsent) {
      await tg(`⚠️ ${ticker(o.symbol)} entry NOT live at broker — ${(o.side||'buy').toUpperCase()} ${o.qty} ${(o.type||'stop').toUpperCase()} @ ${o.price}${o.stop != null ? `, SL ${o.stop}` : ''} was staged but the Send Order click was missed/cancelled. Place it by hand if you still want it.`);
    }
    verify = { verifiedAt: new Date().toISOString(), entries: entryOrders.length, unsent: unsent.length, rows };
    try { fs.writeFileSync(require("path").join(__dirname, "..", "logs", "opening_entry_verify.json"), JSON.stringify(verify)); }
    catch (e) { console.log("could not write entry-verify file:", e.message); }
  }

  sock.close();
  if (DRY) {
    const pass = results.filter(r => r.ok).length;
    console.log(`\nDRY-RUN done: ${pass}/${orders.length} verified (stop attached + neutralized, nothing sent)`);
    results.filter(r => !r.ok).forEach(r => console.log(`   FAIL ${r.symbol}: ${r.reason || 'see log'}`));
  } else
  console.log(`\nqueue done: staged ${results.filter(r => r.staged).length}/${orders.length}`
    + (verify ? `, entries live ${verify.entries - verify.unsent}/${verify.entries}` : "")
    + (reconcile ? `, closes flattened ${reconcile.flattened}/${reconcile.total} (per positions)` : ""));
  process.exit(0);
})().catch(e => { console.error("ERR", e.message); process.exit(3); });
