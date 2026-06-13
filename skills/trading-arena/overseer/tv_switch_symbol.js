#!/usr/bin/env node
/**
 * Switches the TradingView chart symbol via the Chrome DevTools Protocol.
 * Called by tv_focus.py (Python wrapper).
 *
 * Zero dependencies: uses Node 22's built-in global fetch + WebSocket to talk
 * to CDP directly (HTTP /json to discover the tab, then a per-target websocket
 * for Runtime.evaluate). No chrome-remote-interface / node_modules required.
 *
 * Usage: node tv_switch_symbol.js KRAKEN:BTCUSD
 */

const CDP_HOST = '127.0.0.1';
const CDP_PORT = 9222;

const symbol = process.argv[2];
if (!symbol) {
  console.error('Usage: node tv_switch_symbol.js <SYMBOL>');
  process.exit(1);
}

// Escape for safe embedding inside a single-quoted JS string literal.
function jsStr(s) {
  return String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

// Run one CDP command over a websocket and resolve with the matching response.
function cdpEvaluate(wsUrl, expression, timeoutMs = 10000) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    const id = 1;
    const timer = setTimeout(() => {
      try { ws.close(); } catch {}
      reject(new Error('CDP websocket timed out'));
    }, timeoutMs);

    ws.addEventListener('open', () => {
      ws.send(JSON.stringify({
        id,
        method: 'Runtime.evaluate',
        params: { expression, returnByValue: true },
      }));
    });

    ws.addEventListener('message', (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }
      if (msg.id !== id) return; // ignore unrelated CDP events
      clearTimeout(timer);
      try { ws.close(); } catch {}
      if (msg.error) {
        reject(new Error(msg.error.message || 'CDP error'));
      } else {
        resolve(msg.result && msg.result.result ? msg.result.result.value : undefined);
      }
    });

    ws.addEventListener('error', (e) => {
      clearTimeout(timer);
      reject(new Error('websocket error: ' + (e && e.message ? e.message : 'unknown')));
    });
  });
}

(async () => {
  try {
    // 1. Discover open tabs. Each page target carries its own websocket URL.
    let targets;
    try {
      const resp = await fetch(`http://${CDP_HOST}:${CDP_PORT}/json`);
      targets = await resp.json();
    } catch (e) {
      console.error(`Cannot reach Chromium CDP at ${CDP_HOST}:${CDP_PORT} — is it running with --remote-debugging-port=${CDP_PORT}? (${e.message})`);
      process.exit(2);
    }

    // 2. Find the TradingView chart tab.
    const tvTarget = (targets || []).find(
      (t) => t.type === 'page' && typeof t.url === 'string' && t.url.includes('tradingview.com/chart')
    );
    if (!tvTarget || !tvTarget.webSocketDebuggerUrl) {
      console.error('No TradingView chart tab found (expected an open tab on tradingview.com/chart).');
      process.exit(2);
    }

    // 3. Switch the symbol via TradingView's internal chart API.
    const sym = jsStr(symbol);
    const expression = `
      (function() {
        try {
          var w = window.TradingViewApi && window.TradingViewApi._activeChartWidgetWV;
          var chart = w && w.value && w.value();
          if (chart && typeof chart.setSymbol === 'function') {
            chart.setSymbol('${sym}', {});
            return JSON.stringify({ok: true, symbol: '${sym}'});
          }
          return JSON.stringify({ok: false, error: 'no chart api'});
        } catch (e) {
          return JSON.stringify({ok: false, error: e.message});
        }
      })();
    `;

    const value = await cdpEvaluate(tvTarget.webSocketDebuggerUrl, expression);
    console.log(value || JSON.stringify({ ok: false, error: 'no value returned' }));
  } catch (e) {
    console.error('Error:', e.message);
    process.exit(3);
  }
})();
