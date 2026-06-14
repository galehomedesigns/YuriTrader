#!/usr/bin/env node
/**
 * tv_inspect_bracket.js - READ-ONLY map of the order panel's Exits section
 * (Take profit / Stop loss bracket toggles + price fields). Clicks nothing,
 * sets nothing, places nothing. Lets us wire "attach stop-loss at entry".
 *
 * Usage: node tv_inspect_bracket.js [--port 9224]
 */
function arg(name, def) { const i = process.argv.indexOf("--" + name); return i === -1 ? def : process.argv[i + 1]; }
const PORT = arg("port", "9224");

(async () => {
  const tabs = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
  const tv = tabs.find(t => t.type === "page" && t.url && t.url.includes("tradingview.com/chart"));
  if (!tv) { console.error("no chart tab"); process.exit(2); }
  const ws = tv.webSocketDebuggerUrl.replace(/\/\/[^/]+\//, `//127.0.0.1:${PORT}/`);
  const sock = new WebSocket(ws);
  let id = 0; const w = {};
  sock.addEventListener("message", e => { const m = JSON.parse(e.data); if (m.id && w[m.id]) { w[m.id](m); delete w[m.id]; } });
  const call = (method, params = {}) => new Promise(res => { const i = ++id; w[i] = res; sock.send(JSON.stringify({ id: i, method, params })); });
  await new Promise(res => sock.addEventListener("open", res));

  const expr = `(function(){
    var vis = el => !!(el && el.offsetParent !== null && el.getClientRects().length>0);
    // find the section around a label word (Take profit / Stop loss)
    function section(word){
      // the smallest element whose text starts with the word
      var cands = Array.from(document.querySelectorAll('*')).filter(e=>vis(e) && (e.innerText||'').trim().toLowerCase().startsWith(word.toLowerCase()) && (e.innerText||'').length < 60);
      cands.sort((a,b)=>(a.innerText||'').length-(b.innerText||'').length);
      var lab = cands[0];
      if(!lab) return {found:false};
      // climb to a row container that also holds a toggle + a price input
      var row = lab, hops=0;
      while(row && hops<6){
        var toggle = row.querySelector('input[type=checkbox],[role=switch],[class*=switch],[class*=toggle]');
        var price = Array.from(row.querySelectorAll('input')).find(i=>i.getAttribute('inputmode')==='decimal');
        if(toggle && price) break;
        row = row.parentElement; hops++;
      }
      if(!row) return {found:true, rowFound:false, labelText:(lab.innerText||'').replace(/\\s+/g,' ').slice(0,40)};
      var toggle = row.querySelector('input[type=checkbox],[role=switch],[class*=switch],[class*=toggle]');
      var price = Array.from(row.querySelectorAll('input')).find(i=>i.getAttribute('inputmode')==='decimal');
      return {
        found:true,
        labelText:(lab.innerText||'').replace(/\\s+/g,' ').slice(0,40),
        toggleTag: toggle?toggle.tagName:null,
        toggleType: toggle?(toggle.getAttribute('type')||toggle.getAttribute('role')):null,
        toggleClass: toggle?(toggle.className||'').slice(0,40):null,
        toggleChecked: toggle?(toggle.checked||toggle.getAttribute('aria-checked')||toggle.getAttribute('aria-pressed')||false):null,
        priceValue: price?price.value:null,
        priceVisible: price?vis(price):null
      };
    }
    return JSON.stringify({ takeProfit: section('Take profit'), stopLoss: section('Stop loss') }, null, 1);
  })()`;
  const res = await call("Runtime.evaluate", { expression: expr, returnByValue: true });
  console.log(res.result && res.result.result ? res.result.result.value : JSON.stringify(res.result));
  sock.close();
})().catch(e => { console.error("ERR", e.message); process.exit(3); });
