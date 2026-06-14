#!/usr/bin/env node
/**
 * tv_inspect_modify.js - opens the "Modify Order" dialog for a resting STOP
 * order (via its edit-settings-cell-button) and READS the dialog fields so we
 * can build the stop-move. It opens the dialog but SUBMITS NOTHING - the order
 * is unchanged until you submit. Close/Cancel the dialog after.
 *
 * Usage: node tv_inspect_modify.js --symbol AZI [--port 9225]
 */
function arg(name, def) { const i = process.argv.indexOf("--" + name); return i === -1 ? def : process.argv[i + 1]; }
const PORT = arg("port", "9225");
const SYM = (arg("symbol", "") || "").toUpperCase();
if (!SYM) { console.error("--symbol required"); process.exit(1); }

(async () => {
  const tabs = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
  const tv = tabs.find(t => t.type === "page" && t.url && t.url.includes("tradingview.com/chart"));
  if (!tv) { console.error("no chart tab"); process.exit(2); }
  const ws = tv.webSocketDebuggerUrl.replace(/\/\/[^/]+\//, `//127.0.0.1:${PORT}/`);
  const sock = new WebSocket(ws);
  let id = 0; const w = {};
  sock.addEventListener("message", e => { const m = JSON.parse(e.data); if (m.id && w[m.id]) { w[m.id](m); delete w[m.id]; } });
  const call = (method, params = {}) => new Promise(res => { const i = ++id; w[i] = res; sock.send(JSON.stringify({ id: i, method, params })); });
  const evalJs = async (expr, awaitP = false) => { const r = await call("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: awaitP }); return r.result && r.result.result ? r.result.result.value : undefined; };
  await new Promise(res => sock.addEventListener("open", res));

  const expr = `(async () => {
    const sleep = ms => new Promise(r=>setTimeout(r,ms));
    const vis = el => !!(el && el.offsetParent !== null && el.getClientRects().length>0);
    function labelFor(inp){ let lab=inp.getAttribute('aria-label')||inp.placeholder||''; let n=inp,h=0; while(n&&h<4&&!lab){n=n.parentElement;h++;if(!n)break;const t=Array.from(n.childNodes).filter(c=>c.nodeType===3).map(c=>c.textContent.trim()).filter(Boolean).join(' ');const le=n.querySelector('label,[class*=label],[class*=Label]');lab=((le?(le.innerText||''):'')+' '+t).replace(/\\s+/g,' ').trim();} return lab; }
    // make sure Orders tab is up
    const tab = Array.from(document.querySelectorAll('button,[role=button],[role=tab]')).find(b=>vis(b)&&/^orders\\b/i.test((b.innerText||'').trim()));
    if (tab) { tab.click(); await sleep(700); }
    const t = document.querySelector('[data-name=\\"QUESTRADE.orders-table\\"]');
    if (!t) return JSON.stringify({error:'no orders table'});
    // find a QUEUED Stop order row for the symbol
    const row = Array.from(t.querySelectorAll('[role=row],tr')).filter(vis).find(r=>{
      const txt=(r.innerText||'').replace(/\\s+/g,' ').trim().toUpperCase();
      return txt.startsWith('${SYM} ') && /\\bSTOP\\b/.test(txt) && /QUEUED/.test(txt);
    });
    if (!row) return JSON.stringify({error:'no queued STOP order row for ${SYM}'});
    const edit = row.querySelector('[data-name=edit-settings-cell-button]');
    if (!edit) return JSON.stringify({error:'no edit-settings-cell-button on row', rowText:(row.innerText||'').replace(/\\s+/g,' ').slice(0,100)});
    edit.click();
    await sleep(1200);
    // read the modify dialog: visible inputs + buttons
    const inputs = Array.from(document.querySelectorAll('input')).filter(vis).map(e=>({label:labelFor(e), type:e.type, inputmode:e.getAttribute('inputmode')||'', value:(e.value||'').slice(0,12)}));
    const buttons = Array.from(document.querySelectorAll('button,[role=button]')).filter(vis).map(b=>({dn:b.getAttribute('data-name')||'', txt:(b.innerText||'').replace(/\\s+/g,' ').slice(0,22)})).filter(b=>!/toast|cookie|notification/i.test(b.dn) && /modify|save|submit|update|confirm|send|cancel|ok|place/i.test(b.dn+' '+b.txt)).slice(0,12);
    return JSON.stringify({opened:true, dialogInputs:inputs.slice(0,12), dialogButtons:buttons}, null, 1);
  })()`;
  console.log(await evalJs(expr, true) || "{}");
  sock.close();
})().catch(e => { console.error("ERR", e.message); process.exit(3); });
