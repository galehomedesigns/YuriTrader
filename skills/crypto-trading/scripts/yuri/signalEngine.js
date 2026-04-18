/**
 * File: signalEngine.js
 * Ollama AI prompt builder + signal parser
 * Uses Qwen2.5:7b via Tailscale from ASUS Ascent GX10
 */
const http = require('http');
const https = require('https');
const config = require('./config');

class SignalEngine {
  constructor() {
    this.baseUrl = config.ollama.baseUrl;
    this.model = config.ollama.model;
  }

  async getSignal(strategyState, recentCandles, openPosition = null) {
    const prompt = this._buildPrompt(strategyState, recentCandles, openPosition);

    try {
      const response = await this._callOllama(prompt);
      const signal = this._parseResponse(response);

      // Validate signal against bias rules
      if (strategyState.sessionBias === 'LONG' && signal.action === 'sell' && !openPosition) {
        signal.action = 'hold';
        signal.reason = 'Overridden: cannot short when bias is LONG';
      }
      if (strategyState.sessionBias === 'SHORT' && signal.action === 'buy') {
        signal.action = 'hold';
        signal.reason = 'Overridden: cannot go long when bias is SHORT';
      }
      if (!strategyState.retestConfirmed && signal.action !== 'hold') {
        signal.action = 'hold';
        signal.reason = 'Overridden: no retest confirmed yet';
      }

      return signal;
    } catch (e) {
      console.error(`[SignalEngine] Error: ${e.message}`);
      return { action: 'hold', confidence: 0, reason: `AI error: ${e.message}` };
    }
  }

  _buildPrompt(state, candles, position) {
    const positionStr = position
      ? `${position.side} at $${position.entryPrice.toFixed(2)}`
      : 'none';

    const candlesJson = JSON.stringify(candles.slice(-20).map(c => ({
      time: c.time,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
      volume: c.volume,
    })));

    return `You are a crypto trading signal engine for BTC/USD intraday trading.

SESSION CONTEXT:
- Daily 50 SMA: ${state.sma50 ? '$' + state.sma50.toFixed(2) : 'N/A'}
- Current Price vs SMA: ${state.sessionBias === 'LONG' ? 'above' : state.sessionBias === 'SHORT' ? 'below' : 'at'}
- Session Bias: ${state.sessionBias}
- First 5-minute candle HIGH: ${state.firstCandleHigh ? '$' + state.firstCandleHigh.toFixed(2) : 'N/A'}
- First 5-minute candle LOW: ${state.firstCandleLow ? '$' + state.firstCandleLow.toFixed(2) : 'N/A'}
- Breakout direction detected: ${state.breakoutDirection || 'none'}
- Retest confirmed: ${state.retestConfirmed ? 'yes' : 'no'}

LAST 20 x 1-MINUTE CANDLES (oldest to newest):
${candlesJson}

OPEN POSITION: ${positionStr}

Given this context, respond ONLY with valid JSON:
{
  "action": "buy" | "sell" | "hold",
  "confidence": 0.0 to 1.0,
  "reason": "brief explanation under 20 words"
}

Rules:
- NEVER suggest buy if session bias is SHORT
- NEVER suggest sell (short) if session bias is LONG and no open position
- If no retest confirmed, always return hold
- confidence must reflect genuine signal strength`;
  }

  async _callOllama(prompt) {
    return new Promise((resolve, reject) => {
      const url = new URL(`${this.baseUrl}/api/generate`);
      const isHttps = url.protocol === 'https:';
      const client = isHttps ? https : http;

      const postData = JSON.stringify({
        model: this.model,
        prompt,
        stream: false,
        options: { temperature: 0.1, num_predict: 100, num_ctx: 8192 },
      });

      const options = {
        hostname: url.hostname,
        port: url.port || (isHttps ? 443 : 80),
        path: url.pathname,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(postData),
        },
        timeout: config.ollama.timeoutMs,
      };

      const req = client.request(options, (res) => {
        let data = '';
        res.on('data', (chunk) => data += chunk);
        res.on('end', () => {
          try {
            const parsed = JSON.parse(data);
            resolve(parsed.response || '');
          } catch (e) {
            reject(new Error(`Ollama parse error: ${data.substring(0, 200)}`));
          }
        });
      });

      req.on('error', reject);
      req.on('timeout', () => { req.destroy(); reject(new Error('Ollama timeout')); });
      req.write(postData);
      req.end();
    });
  }

  _parseResponse(response) {
    try {
      // Extract JSON from response (may have markdown code blocks)
      const jsonMatch = response.match(/\{[\s\S]*?\}/);
      if (!jsonMatch) {
        return { action: 'hold', confidence: 0, reason: 'No JSON in AI response' };
      }

      const signal = JSON.parse(jsonMatch[0]);

      // Validate fields
      if (!['buy', 'sell', 'hold'].includes(signal.action)) signal.action = 'hold';
      if (typeof signal.confidence !== 'number') signal.confidence = 0;
      signal.confidence = Math.max(0, Math.min(1, signal.confidence));
      if (!signal.reason) signal.reason = 'No reason given';

      return signal;
    } catch (e) {
      return { action: 'hold', confidence: 0, reason: `Parse error: ${e.message}` };
    }
  }
}

module.exports = SignalEngine;
