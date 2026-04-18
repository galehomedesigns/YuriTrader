/**
 * File: orderExecutor.js
 * Order placement, confirmation, and Telegram notifications
 */
const https = require('https');
const config = require('./config');

class OrderExecutor {
  constructor(krakenClient, supabaseClient) {
    this.kraken = krakenClient;
    this.db = supabaseClient;
  }

  async placeOrder(side, quantity, entryPrice, stopLoss, takeProfit, strategyState) {
    try {
      console.log(`[Order] Placing ${side} order: ${quantity} BTC @ ~$${entryPrice.toFixed(2)}`);

      // Place market order
      const result = await this.kraken.addOrder(
        config.trading.krakenPair,
        side,
        'market',
        quantity
      );

      const txid = result.txid ? result.txid[0] : 'unknown';
      console.log(`[Order] Order placed — txid: ${txid}`);

      // Log to Supabase
      const trade = {
        agent: 'yuri',
        pair: config.trading.pair,
        side,
        entry_price: entryPrice,
        quantity,
        stop_loss: stopLoss,
        take_profit: takeProfit,
        status: 'open',
        kraken_order_id: txid,
        session_bias: strategyState.sessionBias,
        first_candle_high: strategyState.firstCandleHigh,
        first_candle_low: strategyState.firstCandleLow,
        daily_sma50: strategyState.sma50,
      };

      const tradeId = await this.db.insert('crypto_trades', trade);

      // Telegram notification
      const msg =
        `<b>CRYPTO TRADE: ${side.toUpperCase()}</b>\n` +
        `━━━━━━━━━━━━━━━━━━━━\n` +
        `BTC/USD — ${quantity.toFixed(8)} BTC @ $${entryPrice.toFixed(2)}\n` +
        `SL: $${stopLoss.toFixed(2)} | TP: $${takeProfit.toFixed(2)}\n` +
        `Bias: ${strategyState.sessionBias} | SMA50: $${(strategyState.sma50 || 0).toFixed(2)}\n` +
        `Order: ${txid}`;

      await this._sendTelegram(msg);

      return { txid, tradeId };
    } catch (e) {
      console.error(`[Order] Failed: ${e.message}`);
      await this._sendTelegram(`<b>CRYPTO ORDER FAILED</b>\n${side} ${quantity} BTC\nError: ${e.message}`);
      throw e;
    }
  }

  async closePosition(trade, currentPrice, reason) {
    try {
      const closeSide = trade.side === 'buy' ? 'sell' : 'buy';
      console.log(`[Order] Closing ${trade.side} position: ${closeSide} ${trade.quantity} BTC — ${reason}`);

      const result = await this.kraken.addOrder(
        config.trading.krakenPair,
        closeSide,
        'market',
        trade.quantity
      );

      const txid = result.txid ? result.txid[0] : 'unknown';

      // Calculate P&L
      let pnl, pnlPct;
      if (trade.side === 'buy') {
        pnl = (currentPrice - trade.entry_price) * trade.quantity;
        pnlPct = ((currentPrice - trade.entry_price) / trade.entry_price) * 100;
      } else {
        pnl = (trade.entry_price - currentPrice) * trade.quantity;
        pnlPct = ((trade.entry_price - currentPrice) / trade.entry_price) * 100;
      }

      // Update Supabase
      await this.db.update('crypto_trades', trade.id, {
        exit_price: currentPrice,
        pnl: parseFloat(pnl.toFixed(4)),
        pnl_pct: parseFloat(pnlPct.toFixed(2)),
        status: reason.includes('Stop loss') ? 'stopped' : 'closed',
        closed_at: new Date().toISOString(),
      });

      const sign = pnl >= 0 ? '+' : '';
      const msg =
        `<b>CRYPTO CLOSE: ${closeSide.toUpperCase()}</b>\n` +
        `━━━━━━━━━━━━━━━━━━━━\n` +
        `BTC/USD — ${trade.quantity.toFixed(8)} BTC @ $${currentPrice.toFixed(2)}\n` +
        `P&L: ${sign}$${pnl.toFixed(2)} (${sign}${pnlPct.toFixed(1)}%)\n` +
        `Reason: ${reason}`;

      await this._sendTelegram(msg);

      return { txid, pnl, pnlPct };
    } catch (e) {
      console.error(`[Order] Close failed: ${e.message}`);
      await this._sendTelegram(`<b>CRYPTO CLOSE FAILED</b>\nError: ${e.message}`);
      throw e;
    }
  }

  async _sendTelegram(message) {
    if (!config.telegram.botToken) return;

    return new Promise((resolve) => {
      const postData = JSON.stringify({
        chat_id: config.telegram.chatId,
        text: message,
        parse_mode: 'HTML',
      });

      const options = {
        hostname: 'api.telegram.org',
        path: `/bot${config.telegram.botToken}/sendMessage`,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(postData),
        },
      };

      const req = https.request(options, (res) => {
        let data = '';
        res.on('data', (chunk) => data += chunk);
        res.on('end', () => resolve(data));
      });
      req.on('error', () => resolve(null));
      req.write(postData);
      req.end();
    });
  }
}

module.exports = OrderExecutor;
