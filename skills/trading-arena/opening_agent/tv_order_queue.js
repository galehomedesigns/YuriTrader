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

  // ── modify-stop: reprice an existing resting stop IN PLACE (no cancel/gap) ──
  if (o.action === 'modify-stop') {
    const otab = Array.from(document.querySelectorAll('button,[role=button],[role=tab]')).find(b=>vis(b)&&/^orders\\b/i.test((b.innerText||'').trim()));
    if (otab) { otab.click(); await sleep(700); }
    const t = document.querySelector('[data-name=\\"QUESTRADE.orders-table\\"]');
    if (!t) return {ok:false, log:['no orders table']};
    const row = Array.from(t.querySelectorAll('[role=row],tr')).filter(vis).find(r=>{
      const txt=(r.innerText||'').replace(/\\s+/g,' ').trim().toUpperCase();
      return txt.startsWith(String(o.symbol).toUpperCase()+' ') && /\\bSTOP\\b/.test(txt) && /QUEUED/.test(txt);
    });
    if (!row) return {ok:false, log:['no resting QUEUED stop order for '+o.symbol]};
    const edit = row.querySelector('[data-name=edit-settings-cell-button]');
    if (!edit) return {ok:false, log:['no Modify button on the '+o.symbol+' stop row']};
    edit.click(); await sleep(1200);
    const pe = Array.from(document.querySelectorAll('input')).filter(e=>vis(e)&&e.type==='text').find(e=>/^price/i.test(labelFor(e)));
    if (!pe) return {ok:false, log:['no Price field in Modify dialog']};
    setInput(pe, o.price); await sleep(300);
    // The Confirm button is grayed while TradingView validates the new price;
    // wait for it to ENABLE so we only prompt the user when it's clickable.
    let cf = null, enabled = false;
    for (let k=0; k<15 && !enabled; k++) { cf = document.querySelector('[data-name=place-and-modify-button]'); enabled = !!(cf && vis(cf) && !cf.disabled && !/disabled/i.test(cf.className||'')); if(!enabled) await sleep(400); }
    if (!cf || !vis(cf)) return {ok:false, log:['Modify Confirm button not visible']};
    return {ok:true, staged:true, summary:'Modify '+o.symbol+' stop -> '+o.price+(enabled?' (Confirm ready — click it)':' (wait for Confirm to enable, then click)'), log:['opened Modify for '+o.symbol+' stop, set price '+o.price+', confirm-enabled='+enabled]};
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
    const pb = document.querySelector('[data-name='+o.side+'-order-button]');
    const mm = pb ? (pb.innerText||'').match(/[0-9][0-9.,]*/) : null;
    const px = mm ? parseFloat(mm[0].replace(/,/g,'')) : null;
    if (!px) return {ok:false,chartSymbol,log:[...log,'could not read live price for marketable close']};
    oprice = (o.side==='sell') ? +(px*0.99).toFixed(2) : +(px*1.01).toFixed(2);
    otype = 'limit';
    log.push('marketable -> limit @ '+oprice+' (live '+px+')');
  }

  // 2) order type
  const tab = Array.from(document.querySelectorAll('button,[role=button],[role=tab]')).find(b=>vis(b)&&(b.innerText||'').trim().toLowerCase()===otype);
  if(!tab) return {ok:false,chartSymbol,log:[...log,'ORDER-TYPE TAB NOT FOUND: '+otype]};
  tab.click(); log.push('type -> '+otype); await sleep(450);
  // 3) side
  const sideEl = document.querySelector('[data-name=side-control-'+o.side+']');
  if(!sideEl) return {ok:false,chartSymbol,log:[...log,'SIDE NOT FOUND']};
  sideEl.click(); log.push('side -> '+o.side); await sleep(350);
  // 4) price
  if(oprice!=null){ const pe=Array.from(document.querySelectorAll('input')).filter(e=>vis(e)&&e.type==='text').find(e=>/^price/i.test(labelFor(e))); if(!pe) return {ok:false,chartSymbol,log:[...log,'PRICE INPUT NOT FOUND']}; setInput(pe,oprice); log.push('price -> '+oprice); await sleep(250); }
  // 5) qty (decimal input after Price; verify via qtyEl mirror)
  const dec=Array.from(document.querySelectorAll('input')).filter(e=>vis(e)&&e.getAttribute('inputmode')==='decimal');
  const pIdx=dec.findIndex(e=>/^price/i.test(labelFor(e)));
  const qEl=(oprice==null||pIdx<0)?dec[0]:dec[pIdx+1];
  if(!qEl) return {ok:false,chartSymbol,log:[...log,'QTY INPUT NOT FOUND']};
  setInput(qEl,o.qty); await sleep(400);
  const mirror=document.querySelector('[data-name=qtyEl]'); const mtxt=mirror?(mirror.innerText||'').trim():null;
  if(mtxt!==String(o.qty)) return {ok:false,chartSymbol,log:[...log,'QTY MIRROR MISMATCH: '+mtxt]};
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
  // 6) submit -> open confirmation (poll + rejection detection); never sends
  const submit=document.querySelector('[data-name=place-and-modify-button]');
  if(!submit) return {ok:false,chartSymbol,log:[...log,'SUBMIT BUTTON NOT FOUND']};
  const before=(submit.innerText||'').replace(/\\s+/g,' ').trim();
  submit.click(); log.push('clicked submit (was "'+before+'")');
  let confirmVisible=false, rejection=null;
  for(let k=0;k<10&&!confirmVisible&&!rejection;k++){
    await sleep(400);
    const sb=Array.from(document.querySelectorAll('button,[role=button]')).find(b=>/send order|confirm/i.test(b.innerText||'')); confirmVisible=vis(sb);
    const rj=Array.from(document.querySelectorAll('*')).find(e=>vis(e)&&/was rejected|both marketable|order rejected|please modify/i.test(e.innerText||'')&&(e.innerText||'').length<220); if(rj) rejection=(rj.innerText||'').replace(/\\s+/g,' ').trim().slice(0,180);
  }
  if(rejection) return {ok:false,chartSymbol,log:[...log,'ORDER REJECTED: '+rejection]};
  if(!confirmVisible){ const after=(document.querySelector('[data-name=place-and-modify-button]')||{}).innerText||''; return {ok:false,chartSymbol,log:[...log,'NO confirmation after submit; button now "'+after.replace(/\\s+/g,' ').trim()+'"']}; }
  return { ok:true, staged:true, chartSymbol, summary:before, log };
}`;

const CONFIRM_VISIBLE_FN = `(function(){ var b=Array.from(document.querySelectorAll('button,[role=button]')).find(x=>/send order|confirm/i.test(x.innerText||'')); return !!(b && b.offsetParent!==null && b.getClientRects().length>0); })()`;

(async () => {
  const tabs = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
  const tv = tabs.find(t => t.type === "page" && t.url && t.url.includes("tradingview.com/chart"));
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

  const results = [];
  for (let i = 0; i < orders.length; i++) {
    const o = orders[i];
    const desc = o.action === 'modify-stop'
      ? `modify ${o.symbol} stop -> ${o.price}`
      : `${o.side} ${o.qty} ${o.symbol} ${o.type} @ ${o.price}${o.stop != null ? ' (SL ' + o.stop + ')' : ''}`;
    console.log(`\n=== [${i + 1}/${orders.length}] staging ${desc} ===`);
    const staged = await evalJs(`(${STAGE_FN})(${JSON.stringify(o)})`, true);
    staged.log.forEach(l => console.log("   " + l));
    if (!staged.ok) { console.log("   !! could not stage - skipping to next"); results.push({ ...o, staged: false }); continue; }
    console.log(`   >> CONFIRM ON SCREEN: ${staged.summary}  (click Send Order, or Cancel to skip)`);

    const start = Date.now();
    let closed = false;
    await sleep(300);
    while (Date.now() - start < PER_ORDER_TIMEOUT_MS) {
      const stillOpen = await evalJs(CONFIRM_VISIBLE_FN);
      if (!stillOpen) { closed = true; break; }
      await sleep(350);
    }
    if (!closed) { console.log("   .. timed out waiting for your confirm - stopping queue"); results.push({ ...o, staged: true, confirmedClosed: false }); break; }
    console.log("   .. confirmation closed - advancing");
    results.push({ ...o, staged: true, confirmedClosed: true });
    await sleep(400);
  }

  sock.close();
  console.log(`\nqueue done: staged ${results.filter(r => r.staged).length}/${orders.length}`);
  process.exit(0);
})().catch(e => { console.error("ERR", e.message); process.exit(3); });
