/**
 * File: riskManager.js
 * Position sizing, stop-loss, take-profit, daily loss limit
 */
const config = require('./config');

class RiskManager {
  constructor() {
    this.sessionStartBalance = null;
    this.halted = false;
  }

  setSessionStartBalance(balance) {
    this.sessionStartBalance = balance;
    this.halted = false;
  }

  calculatePositionSize(balance, entryPrice, stopLossPrice) {
    const maxUSD = balance * config.risk.maxPositionPct;
    const qty = maxUSD / entryPrice;

    // Ensure minimum order value
    const orderValue = qty * entryPrice;
    if (orderValue < config.risk.minOrderUSD) {
      return 0;
    }

    return parseFloat(qty.toFixed(8));
  }

  checkDailyLossLimit(currentBalance) {
    if (!this.sessionStartBalance) return false;
    const lossPct = (this.sessionStartBalance - currentBalance) / this.sessionStartBalance;
    if (lossPct >= config.risk.dailyLossLimitPct) {
      this.halted = true;
      return true;
    }
    return false;
  }

  calculateStopLoss(side, entryPrice) {
    if (side === 'buy') {
      return parseFloat((entryPrice * (1 - config.risk.stopLossPct)).toFixed(2));
    }
    return parseFloat((entryPrice * (1 + config.risk.stopLossPct)).toFixed(2));
  }

  calculateTakeProfit(side, entryPrice) {
    if (side === 'buy') {
      return parseFloat((entryPrice * (1 + config.risk.takeProfitPct)).toFixed(2));
    }
    return parseFloat((entryPrice * (1 - config.risk.takeProfitPct)).toFixed(2));
  }

  isHalted() {
    return this.halted;
  }
}

module.exports = RiskManager;
