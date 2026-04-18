/**
 * File: krakenClient.js
 * Kraken REST + WebSocket wrapper
 */
const https = require('https');
const qs = require('querystring');
const WebSocket = require('ws');
const { generateKrakenSignature, getNonce } = require('./utils/krakenAuth');
const config = require('./config');

class KrakenClient {
  constructor() {
    this.apiKey = config.kraken.apiKey;
    this.apiSecret = config.kraken.apiSecret;
    this.restBase = config.kraken.restBase;
    this.ws = null;
    this.candleCallbacks = [];
  }

  // ── REST API ──

  async publicRequest(endpoint, params = {}) {
    const query = Object.keys(params).length ? '?' + qs.stringify(params) : '';
    const url = `${this.restBase}${endpoint}${query}`;

    return new Promise((resolve, reject) => {
      https.get(url, (res) => {
        let data = '';
        res.on('data', (chunk) => data += chunk);
        res.on('end', () => {
          try {
            const parsed = JSON.parse(data);
            if (parsed.error && parsed.error.length > 0) {
              reject(new Error(`Kraken: ${parsed.error.join(', ')}`));
            } else {
              resolve(parsed.result);
            }
          } catch (e) {
            reject(e);
          }
        });
      }).on('error', reject);
    });
  }

  async privateRequest(endpoint, params = {}) {
    const nonce = getNonce();
    const body = { ...params, nonce };
    const signature = generateKrakenSignature(endpoint, body, this.apiSecret);
    const postData = qs.stringify(body);

    return new Promise((resolve, reject) => {
      const options = {
        hostname: 'api.kraken.com',
        path: endpoint,
        method: 'POST',
        headers: {
          'API-Key': this.apiKey,
          'API-Sign': signature,
          'Content-Type': 'application/x-www-form-urlencoded',
          'Content-Length': Buffer.byteLength(postData),
        },
      };

      const req = https.request(options, (res) => {
        let data = '';
        res.on('data', (chunk) => data += chunk);
        res.on('end', () => {
          try {
            const parsed = JSON.parse(data);
            if (parsed.error && parsed.error.length > 0) {
              reject(new Error(`Kraken: ${parsed.error.join(', ')}`));
            } else {
              resolve(parsed.result);
            }
          } catch (e) {
            reject(e);
          }
        });
      });
      req.on('error', reject);
      req.write(postData);
      req.end();
    });
  }

  // ── Market Data ──

  async getOHLC(pair, interval = 1440, since = null) {
    const params = { pair, interval };
    if (since) params.since = since;
    return this.publicRequest('/0/public/OHLC', params);
  }

  async getTicker(pair) {
    return this.publicRequest('/0/public/Ticker', { pair });
  }

  // ── Account ──

  async getBalance() {
    return this.privateRequest('/0/private/Balance');
  }

  async getTradeBalance(asset = 'ZUSD') {
    return this.privateRequest('/0/private/TradeBalance', { asset });
  }

  async getOpenOrders() {
    return this.privateRequest('/0/private/OpenOrders');
  }

  async getOpenPositions() {
    return this.privateRequest('/0/private/OpenPositions');
  }

  // ── Orders ──

  async addOrder(pair, type, ordertype, volume, price = null, opts = {}) {
    const params = {
      pair,
      type,       // 'buy' or 'sell'
      ordertype,  // 'market', 'limit', 'stop-loss', etc.
      volume: volume.toString(),
      ...opts,
    };
    if (price) params.price = price.toString();
    return this.privateRequest('/0/private/AddOrder', params);
  }

  async cancelOrder(txid) {
    return this.privateRequest('/0/private/CancelOrder', { txid });
  }

  // ── WebSocket ──

  connectWebSocket(onCandle) {
    const wsUrl = config.kraken.wsUrl;
    this.ws = new WebSocket(wsUrl);

    this.ws.on('open', () => {
      console.log('[Kraken WS] Connected');
      // Subscribe to 1-minute candles for BTC/USD
      this.ws.send(JSON.stringify({
        method: 'subscribe',
        params: {
          channel: 'ohlc',
          symbol: [config.trading.wsPair],
          interval: config.trading.candleInterval,
        },
      }));
    });

    this.ws.on('message', (data) => {
      try {
        const msg = JSON.parse(data);
        if (msg.channel === 'ohlc' && msg.type === 'update') {
          for (const candle of msg.data) {
            onCandle({
              time: candle.timestamp,
              open: parseFloat(candle.open),
              high: parseFloat(candle.high),
              low: parseFloat(candle.low),
              close: parseFloat(candle.close),
              volume: parseFloat(candle.volume),
              vwap: parseFloat(candle.vwap),
            });
          }
        }
      } catch (e) {
        // Heartbeats and other messages — ignore
      }
    });

    this.ws.on('error', (err) => {
      console.error('[Kraken WS] Error:', err.message);
    });

    this.ws.on('close', () => {
      console.log('[Kraken WS] Disconnected — reconnecting in 5s...');
      setTimeout(() => this.connectWebSocket(onCandle), 5000);
    });
  }

  closeWebSocket() {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }
}

module.exports = KrakenClient;
