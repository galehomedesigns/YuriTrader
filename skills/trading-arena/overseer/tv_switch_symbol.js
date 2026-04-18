#!/usr/bin/env node
/**
 * Switches the TradingView chart symbol via CDP.
 * Called by tv_focus.py (Python wrapper).
 *
 * Usage: node tv_switch_symbol.js KRAKEN:BTCUSD
 */
import CDP from '/docker/openclaw-xrt9/data/tradingview-mcp/node_modules/chrome-remote-interface/index.js';

const symbol = process.argv[2];
if (!symbol) {
  console.error('Usage: node tv_switch_symbol.js <SYMBOL>');
  process.exit(1);
}

(async () => {
  let client;
  try {
    client = await CDP({ host: '127.0.0.1', port: 9222 });
    const { Runtime, Target } = client;

    // Find the TradingView tab target
    const targets = await Target.getTargets();
    const tvTarget = targets.targetInfos.find(
      t => t.type === 'page' && t.url.includes('tradingview.com/chart')
    );
    if (!tvTarget) {
      console.error('No TradingView chart tab found');
      process.exit(2);
    }

    // Attach to that target
    await client.close();
    client = await CDP({ host: '127.0.0.1', port: 9222, target: tvTarget.id });
    const { Runtime: R } = client;
    await R.enable();

    // Use TradingView's internal API to set the symbol (matches MCP behavior)
    const expression = `
      (function() {
        try {
          var chart = window.TradingViewApi && window.TradingViewApi._activeChartWidgetWV.value();
          if (chart && typeof chart.setSymbol === 'function') {
            chart.setSymbol('${symbol}', {});
            return JSON.stringify({ok: true, symbol: '${symbol}'});
          }
          return JSON.stringify({ok: false, error: 'no chart api'});
        } catch (e) {
          return JSON.stringify({ok: false, error: e.message});
        }
      })();
    `;
    const result = await R.evaluate({ expression, returnByValue: true });
    console.log(result.result.value || JSON.stringify(result));
  } catch (e) {
    console.error('Error:', e.message);
    process.exit(3);
  } finally {
    if (client) await client.close();
  }
})();
