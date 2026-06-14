#!/usr/bin/env node
/**
 * tv_inspect.js - READ-ONLY inspector for the TradingView order panel.
 *
 * Clicks NOTHING, sets NO inputs, places NO orders. It only reads the DOM so we
 * can finalize the submit flow + symbol detection for tv_order.js. Safe to run.
 *
 * Usage: node tv_inspect.js [--port 9224]
 */
function arg(name, def) {
  const i = process.argv.indexOf("--" + name);
  return i === -1 ? def : process.argv[i + 1];
}
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
    function info(sel){var e=document.querySelector(sel); if(!e)return null; var r=e.getBoundingClientRect(); return {x:Math.round(r.x), y:Math.round(r.y), txt:(e.innerText||'').replace(/\\s+/g,' ').slice(0,28), vis:vis(e), aria:(e.getAttribute('aria-pressed')||e.getAttribute('aria-checked')||e.getAttribute('aria-selected')||null)};}
    // 'Start creating order' / submit button
    var sco = Array.from(document.querySelectorAll('button,[role=button]')).find(b=>vis(b) && /start creating order|create order|send order|place order/i.test(b.innerText||''));
    var scoInfo = sco?{x:Math.round(sco.getBoundingClientRect().x), tag:sco.tagName, dn:sco.getAttribute('data-name'), cls:(sco.className||'').slice(0,50), disabled:(sco.disabled||sco.getAttribute('aria-disabled')||false), txt:(sco.innerText||'').replace(/\\s+/g,' ').slice(0,30)}:null;
    // try to read the ORDER TICKET symbol (right panel header). Look for the ticket widget.
    var symGuess = null;
    var tickets = Array.from(document.querySelectorAll('[class*=order],[class*=Order],[class*=ticket],[class*=Ticket]')).filter(vis);
    for(var i=0;i<tickets.length && !symGuess;i++){
      var m=(tickets[i].innerText||'').match(/\\b[A-Z]{1,6}(\\.[A-Z]{1,3})?\\b/);
    }
    // broker account symbol via any [data-symbol] or the active order widget title
    var symEl = document.querySelector('[data-name=order-panel]');
    return JSON.stringify({
      buyOrderButton: info('[data-name=buy-order-button]'),
      sellOrderButton: info('[data-name=sell-order-button]'),
      sideControlBuy: info('[data-name=side-control-buy]'),
      sideControlSell: info('[data-name=side-control-sell]'),
      submitButton: scoInfo,
      windowInnerWidth: window.innerWidth,
      // dump the right-third panel text to spot the symbol + Buy/Sell layout
      rightPanelText: (function(){
        var els = Array.from(document.querySelectorAll('div')).filter(d=>{var r=d.getBoundingClientRect(); return r.x > window.innerWidth*0.66 && r.width>120 && r.width<480 && r.height>200 && vis(d);});
        els.sort((a,b)=>b.getBoundingClientRect().height-a.getBoundingClientRect().height);
        return els[0]?(els[0].innerText||'').replace(/\\s+/g,' ').slice(0,300):'(right panel not found)';
      })()
    }, null, 1);
  })()`;
  const res = await call("Runtime.evaluate", { expression: expr, returnByValue: true });
  console.log(res.result && res.result.result ? res.result.result.value : JSON.stringify(res.result));
  sock.close();
})().catch(e => { console.error("ERR", e.message); process.exit(3); });
