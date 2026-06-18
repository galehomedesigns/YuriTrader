#!/usr/bin/env node
/**
 * tv_ui_probe.js - READ-ONLY-ish verification of every order-ticket control
 * location used by tv_order.js / tv_order_queue.js. It opens the ticket, clicks
 * through each order TYPE, locates side/price/qty/stop-loss/take-profit, toggles
 * the bracket switches on then OFF again, and locates the submit button.
 *
 * It NEVER clicks Send Order / Confirm — nothing is transmitted.
 * Usage: node tv_ui_probe.js [--port 9225]
 */
const tab = require("./tv_tab");
function arg(n, d) { const i = process.argv.indexOf("--" + n); return i > -1 ? process.argv[i + 1] : d; }
const PORT = arg("port", "9225");
const sleep = ms => new Promise(r => setTimeout(r, ms));
function mkConn(u) { const s = new WebSocket(u); let id = 0; const w = {}; s.addEventListener("message", e => { const m = JSON.parse(e.data); if (m.id && w[m.id]) { w[m.id](m); delete w[m.id]; } }); const call = (method, params = {}) => new Promise(r => { const i = ++id; w[i] = r; s.send(JSON.stringify({ id: i, method, params })); }); const ready = new Promise(r => s.addEventListener("open", r)); return { call, ready, sock: s }; }

(async () => {
  const tabs = await fetch(`http://127.0.0.1:${PORT}/json`).then(r => r.json());
  const tr = tab.pickTradingTab(tabs);
  const P = mkConn(tr.webSocketDebuggerUrl.replace(/\/\/[^/]+\//, `//127.0.0.1:${PORT}/`)); await P.ready;
  const ev = async expr => (await P.call("Runtime.evaluate", { expression: expr, returnByValue: true })).result.result.value;
  const J = async expr => JSON.parse(await ev(expr) || "null");

  // shared page-side helpers mirroring the order tools
  const HELPERS = `
    function vis(e){if(!e)return false;var s=getComputedStyle(e);return s.display!=="none"&&s.visibility!=="hidden"&&!!e.offsetParent;}
    function labelFor(inp){let lab=inp.getAttribute('aria-label')||inp.placeholder||'';let n=inp,h=0;while(n&&h<4&&!lab){n=n.parentElement;h++;if(!n)break;const t=Array.from(n.childNodes).filter(c=>c.nodeType===3).map(c=>c.textContent.trim()).filter(Boolean).join(' ');const le=n.querySelector('label,[class*=label],[class*=Label]');lab=((le?(le.innerText||''):'')+' '+t).replace(/\\s+/g,' ').trim();}return lab;}
    function findSection(word){let c=Array.from(document.querySelectorAll('*')).filter(e=>vis(e)&&(e.innerText||'').trim().toLowerCase().startsWith(word.toLowerCase())&&(e.innerText||'').length<60);c.sort((a,b)=>(a.innerText||'').length-(b.innerText||'').length);let lab=c[0];if(!lab)return null;let row=lab,h=0;while(row&&h<6){const t=row.querySelector('[class*=switchContainer],input[type=checkbox],[role=switch]');const p=Array.from(row.querySelectorAll('input')).find(i=>i.getAttribute('inputmode')==='decimal');if(t&&p)return{toggle:t,price:p};row=row.parentElement;h++;}return null;}
    function findOrderTypeTab(targetType){var norm=targetType.trim().toLowerCase();var aliases={'stop':['stop','stop order','stp'],'limit':['limit','limit order','lmt'],'market':['market','market order','mkt'],'stop limit':['stop limit','stop-limit','stop limit order','stplmt']};var cand=aliases[norm]||[norm];var vb=Array.from(document.querySelectorAll('button,[role=button],[role=tab],[data-name*=order-type]')).filter(vis);for(var b of vb){var t=(b.innerText||'').trim().toLowerCase();if(cand.includes(t))return b;}for(var b of vb){var t=(b.innerText||'').trim().toLowerCase();for(var c of cand)if(t.startsWith(c))return b;}return null;}
    function findSubmit(){var b=document.querySelector('[data-name=place-and-modify-button]');if(b&&vis(b))return b;b=document.querySelector('[data-name*=place-button],[data-name*=submit-button],[data-name*=order-button]');if(b&&vis(b))return b;var all=Array.from(document.querySelectorAll('button,[role=button]')).filter(vis);b=all.find(x=>{var t=(x.innerText||'').trim().toLowerCase();return /^(buy|sell)\\s/.test(t)&&!/send order|cancel/i.test(t);});return b||null;}
  `;
  const out = [];
  const log = (k, v) => out.push({ item: k, ...v });

  // 1) order ticket open (the "Trade" button)
  let r = await J(`(function(){${HELPERS}
    var up=function(){return !!(document.querySelector('[data-name=place-and-modify-button]')||document.querySelector('[data-name^=side-control]'));};
    var wasOpen=up();
    var tradeBtn=Array.from(document.querySelectorAll('button,[role=button]')).find(b=>vis(b)&&/^trade$/i.test((b.innerText||'').trim()));
    return JSON.stringify({wasOpen:wasOpen, tradeBtnFound:!!tradeBtn, tradeBtnText:tradeBtn?(tradeBtn.innerText||'').trim():null});})()`);
  log("Order/Trade button (opens ticket)", { found: r.tradeBtnFound, detail: `text="${r.tradeBtnText}" ticketAlreadyOpen=${r.wasOpen}` });
  // open it if needed
  await ev(`(function(){${HELPERS}var up=function(){return !!(document.querySelector('[data-name=place-and-modify-button]')||document.querySelector('[data-name^=side-control]'));};if(!up()){var b=Array.from(document.querySelectorAll('button,[role=button]')).find(x=>vis(x)&&/^trade$/i.test((x.innerText||'').trim()));if(b)b.click();}return 'ok';})()`);
  await sleep(2500);

  // 2) order TYPE options — click each, confirm it activates + which price fields appear
  for (const t of ["market", "limit", "stop", "stop limit"]) {
    r = await J(`(function(){${HELPERS}
      var tabEl=findOrderTypeTab(${JSON.stringify(t)});
      if(!tabEl)return JSON.stringify({found:false});
      tabEl.click();
      return JSON.stringify({found:true,text:(tabEl.innerText||'').trim(),dn:tabEl.getAttribute('data-name')||null});})()`);
    await sleep(700);
    const fields = await J(`(function(){${HELPERS}
      var dec=Array.from(document.querySelectorAll('input')).filter(e=>vis(e)&&e.getAttribute('inputmode')==='decimal');
      var priceIdx=dec.findIndex(e=>/^price/i.test(labelFor(e)));
      var hasLimit=dec.some(e=>/limit/i.test(labelFor(e)));
      return JSON.stringify({decimalInputs:dec.length,priceFieldPresent:priceIdx>=0,limitFieldPresent:hasLimit});})()`);
    log(`Order-type: ${t.toUpperCase()}`, { found: r.found, detail: r.found ? `tab text="${r.text}" -> decimalInputs=${fields.decimalInputs} priceField=${fields.priceFieldPresent} limitField=${fields.limitFieldPresent}` : "TAB NOT FOUND" });
  }

  // set to STOP for the remaining field tests (has both price trigger + qty + brackets)
  await ev(`(function(){${HELPERS}var t=findOrderTypeTab('stop');if(t)t.click();return 'ok';})()`); await sleep(800);

  // 3) side controls buy / sell  (+ any extra like spread/"down")
  r = await J(`(function(){${HELPERS}
    var sc=Array.from(document.querySelectorAll('[data-name^=side-control]')).map(e=>({dn:e.getAttribute('data-name'),text:(e.innerText||'').trim(),vis:vis(e)}));
    var buy=document.querySelector('[data-name=side-control-buy]');var sell=document.querySelector('[data-name=side-control-sell]');
    return JSON.stringify({all:sc,buyFound:!!buy,sellFound:!!sell});})()`);
  log("Side: BUY", { found: r.buyFound, detail: "data-name=side-control-buy" });
  log("Side: SELL", { found: r.sellFound, detail: "data-name=side-control-sell" });
  log("Side: all controls present", { found: r.all.length > 0, detail: JSON.stringify(r.all) });

  // 4) price field
  r = await J(`(function(){${HELPERS}
    var pe=Array.from(document.querySelectorAll('input')).filter(e=>vis(e)&&e.type==='text').find(e=>/^price/i.test(labelFor(e)));
    if(!pe)pe=Array.from(document.querySelectorAll('input')).filter(e=>vis(e)&&e.getAttribute('inputmode')==='decimal').find(e=>/price/i.test(labelFor(e)));
    return JSON.stringify({found:!!pe,label:pe?labelFor(pe):null,value:pe?pe.value:null});})()`);
  log("Price field", { found: r.found, detail: r.found ? `label="${r.label}" current="${r.value}"` : "NOT FOUND" });

  // 5) quantity (shares) field = decimal input after price
  r = await J(`(function(){${HELPERS}
    var dec=Array.from(document.querySelectorAll('input')).filter(e=>vis(e)&&e.getAttribute('inputmode')==='decimal');
    var pi=dec.findIndex(e=>/^price/i.test(labelFor(e)));
    var q=(pi<0)?dec[0]:dec[pi+1];
    var mirror=document.querySelector('[data-name=qtyEl]');
    return JSON.stringify({found:!!q,label:q?labelFor(q):null,value:q?q.value:null,qtyMirrorFound:!!mirror,mirrorText:mirror?(mirror.innerText||'').trim():null});})()`);
  log("Quantity (shares) field", { found: r.found, detail: r.found ? `label="${r.label}" value="${r.value}"` : "NOT FOUND" });
  log("Quantity mirror (qtyEl verify)", { found: r.qtyMirrorFound, detail: `shows="${r.mirrorText}"` });

  // 6) STOP LOSS section: toggle + price. Toggle ON, verify, toggle OFF.
  for (const sec of ["Stop loss", "Take profit"]) {
    // expand Exits if needed
    await ev(`(function(){${HELPERS}
      if(!findSection(${JSON.stringify(sec)})){var fe=function(){return Array.from(document.querySelectorAll('*')).filter(e=>vis(e)&&(e.innerText||'').trim().toLowerCase()==='exits'&&(e.innerText||'').length<12)[0];};for(var a=0;a<2;a++){var h=fe();if(!h)break;(h.closest('[role=button]')||h.parentElement||h).click();}}
      return 'ok';})()`); await sleep(700);
    const found = await J(`(function(){${HELPERS}var s=findSection(${JSON.stringify(sec)});if(!s)return JSON.stringify({found:false});var cb=s.toggle.querySelector('input[type=checkbox]')||(s.toggle.tagName==='INPUT'?s.toggle:null);return JSON.stringify({found:true,toggleFound:!!cb,wasChecked:cb?cb.checked:null,priceFieldFound:!!s.price});})()`);
    if (found.found) {
      // toggle ON
      const onState = await J(`(function(){${HELPERS}var s=findSection(${JSON.stringify(sec)});var cb=s.toggle.querySelector('input[type=checkbox]')||(s.toggle.tagName==='INPUT'?s.toggle:null);if(cb&&!cb.checked)cb.click();return JSON.stringify({checked:cb?cb.checked:null});})()`); await sleep(500);
      // toggle OFF (restore)
      const offState = await J(`(function(){${HELPERS}var s=findSection(${JSON.stringify(sec)});var cb=s.toggle.querySelector('input[type=checkbox]')||(s.toggle.tagName==='INPUT'?s.toggle:null);if(cb&&cb.checked)cb.click();return JSON.stringify({checked:cb?cb.checked:null});})()`); await sleep(400);
      log(`${sec} toggle + price`, { found: true, detail: `toggleFound=${found.toggleFound} priceField=${found.priceFieldFound} | toggled ON->${onState.checked} then OFF->${offState.checked}` });
    } else {
      log(`${sec} toggle + price`, { found: false, detail: "SECTION NOT FOUND" });
    }
  }

  // 7) submit / place button — LOCATE ONLY, do not click
  r = await J(`(function(){${HELPERS}var b=findSubmit();return JSON.stringify({found:!!b,text:b?(b.innerText||'').replace(/\\s+/g,' ').trim():null,dn:b?b.getAttribute('data-name'):null});})()`);
  log("Submit / Place button (located, NOT clicked)", { found: r.found, detail: r.found ? `text="${r.text}" data-name=${r.dn}` : "NOT FOUND" });

  P.sock.close();
  // print report
  console.log("\n  RESULT                                              FOUND  DETAIL");
  console.log("  " + "-".repeat(110));
  for (const o of out) {
    const mark = o.found ? " ✅  " : " ❌  ";
    console.log("  " + o.item.padEnd(48).slice(0, 48) + mark + (o.detail || ""));
  }
})().catch(e => { console.error("ERR", e.message); process.exit(3); });
