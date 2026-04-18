/**
 * File: strategyEngine.js
 * 50 SMA Daily Trend Filter + First 5-Minute Candle Breakout
 */
const config = require('./config');

class StrategyEngine {
  constructor() {
    this.sessionBias = 'NEUTRAL';
    this.sma50Value = null;
    this.firstCandleHigh = null;
    this.firstCandleLow = null;
    this.firstCandleLocked = false;
    this.firstCandleCandles = [];
    this.breakoutDetected = false;
    this.breakoutDirection = null;
    this.retestConfirmed = false;
    this.breakoutPrice = null;
  }

  // ── Layer 1: Daily 50 SMA Trend Filter ──

  calculateSMA50(dailyCandles) {
    if (dailyCandles.length < config.trading.smaPeriod) {
      console.log(`[Strategy] Not enough daily candles (${dailyCandles.length}/${config.trading.smaPeriod})`);
      this.sessionBias = 'NEUTRAL';
      return;
    }

    const closes = dailyCandles
      .slice(-config.trading.smaPeriod)
      .map(c => parseFloat(c[4])); // close price is index 4 in Kraken OHLC

    const sum = closes.reduce((a, b) => a + b, 0);
    this.sma50Value = sum / closes.length;

    const currentPrice = parseFloat(dailyCandles[dailyCandles.length - 1][4]);

    if (currentPrice > this.sma50Value) {
      this.sessionBias = 'LONG';
    } else if (currentPrice < this.sma50Value) {
      this.sessionBias = 'SHORT';
    } else {
      this.sessionBias = 'NEUTRAL';
    }

    console.log(`[Strategy] SMA50: $${this.sma50Value.toFixed(2)} | Price: $${currentPrice.toFixed(2)} | Bias: ${this.sessionBias}`);
  }

  // ── Layer 2: First 5-Minute Candle Box ──

  addFirstCandleData(candle) {
    if (this.firstCandleLocked) return;
    this.firstCandleCandles.push(candle);
  }

  lockFirstCandle() {
    if (this.firstCandleCandles.length === 0) {
      console.log('[Strategy] No candles to lock first candle range');
      return false;
    }

    this.firstCandleHigh = Math.max(...this.firstCandleCandles.map(c => c.high));
    this.firstCandleLow = Math.min(...this.firstCandleCandles.map(c => c.low));
    this.firstCandleLocked = true;

    console.log(`[Strategy] First candle locked — High: $${this.firstCandleHigh.toFixed(2)} | Low: $${this.firstCandleLow.toFixed(2)}`);
    return true;
  }

  // ── Layer 3: Breakout + Retest Detection ──

  evaluateCandle(candle) {
    if (!this.firstCandleLocked || this.sessionBias === 'NEUTRAL') {
      return { signal: 'hold', reason: 'Waiting for setup' };
    }

    const close = candle.close;
    const retestThreshold = 0.001; // 0.1%

    // Detect breakout
    if (!this.breakoutDetected) {
      if (this.sessionBias === 'LONG' && close > this.firstCandleHigh) {
        this.breakoutDetected = true;
        this.breakoutDirection = 'UP';
        this.breakoutPrice = this.firstCandleHigh;
        console.log(`[Strategy] Breakout UP detected at $${close.toFixed(2)}`);
        return { signal: 'hold', reason: 'Breakout detected, waiting for retest' };
      }

      if (this.sessionBias === 'SHORT' && close < this.firstCandleLow) {
        this.breakoutDetected = true;
        this.breakoutDirection = 'DOWN';
        this.breakoutPrice = this.firstCandleLow;
        console.log(`[Strategy] Breakout DOWN detected at $${close.toFixed(2)}`);
        return { signal: 'hold', reason: 'Breakout detected, waiting for retest' };
      }

      return { signal: 'hold', reason: 'No breakout yet' };
    }

    // Detect retest
    if (this.breakoutDetected && !this.retestConfirmed) {
      if (this.breakoutDirection === 'UP') {
        const distFromLevel = Math.abs(candle.low - this.breakoutPrice) / this.breakoutPrice;
        if (distFromLevel <= retestThreshold && close > this.breakoutPrice) {
          this.retestConfirmed = true;
          console.log(`[Strategy] Retest confirmed — LONG entry signal`);
          return {
            signal: 'buy',
            reason: `Breakout UP + retest confirmed at $${this.breakoutPrice.toFixed(2)}`,
            entryPrice: close,
          };
        }
        // False breakout — price fell back into range
        if (close < this.firstCandleLow) {
          this.breakoutDetected = false;
          this.breakoutDirection = null;
          console.log('[Strategy] False breakout — reset');
          return { signal: 'hold', reason: 'False breakout, reset' };
        }
      }

      if (this.breakoutDirection === 'DOWN') {
        const distFromLevel = Math.abs(candle.high - this.breakoutPrice) / this.breakoutPrice;
        if (distFromLevel <= retestThreshold && close < this.breakoutPrice) {
          this.retestConfirmed = true;
          console.log(`[Strategy] Retest confirmed — SHORT entry signal`);
          return {
            signal: 'sell',
            reason: `Breakout DOWN + retest confirmed at $${this.breakoutPrice.toFixed(2)}`,
            entryPrice: close,
          };
        }
        if (close > this.firstCandleHigh) {
          this.breakoutDetected = false;
          this.breakoutDirection = null;
          console.log('[Strategy] False breakout — reset');
          return { signal: 'hold', reason: 'False breakout, reset' };
        }
      }

      return { signal: 'hold', reason: 'Waiting for retest' };
    }

    return { signal: 'hold', reason: 'Signal already consumed' };
  }

  // ── Position Monitoring ──

  checkExit(side, entryPrice, stopLoss, takeProfit, currentPrice) {
    if (side === 'buy') {
      if (currentPrice <= stopLoss) return { exit: true, reason: `Stop loss hit ($${currentPrice.toFixed(2)})` };
      if (currentPrice >= takeProfit) return { exit: true, reason: `Take profit hit ($${currentPrice.toFixed(2)})` };
    } else {
      if (currentPrice >= stopLoss) return { exit: true, reason: `Stop loss hit ($${currentPrice.toFixed(2)})` };
      if (currentPrice <= takeProfit) return { exit: true, reason: `Take profit hit ($${currentPrice.toFixed(2)})` };
    }
    return { exit: false };
  }

  resetSession() {
    this.firstCandleHigh = null;
    this.firstCandleLow = null;
    this.firstCandleLocked = false;
    this.firstCandleCandles = [];
    this.breakoutDetected = false;
    this.breakoutDirection = null;
    this.retestConfirmed = false;
    this.breakoutPrice = null;
    console.log('[Strategy] Session reset');
  }

  getState() {
    return {
      sessionBias: this.sessionBias,
      sma50: this.sma50Value,
      firstCandleHigh: this.firstCandleHigh,
      firstCandleLow: this.firstCandleLow,
      locked: this.firstCandleLocked,
      breakoutDetected: this.breakoutDetected,
      breakoutDirection: this.breakoutDirection,
      retestConfirmed: this.retestConfirmed,
    };
  }
}

module.exports = StrategyEngine;
