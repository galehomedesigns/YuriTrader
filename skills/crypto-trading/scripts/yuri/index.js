/**
 * File: index.js
 * Yuri Crypto Agent — Main loop
 *
 * Strategy: 50 SMA Daily Trend Filter + First 5-Minute Candle Breakout
 * Exchange: Kraken (BTC/USD)
 * AI: Ollama Qwen2.5:7b via Tailscale
 */
const config = require('./config');
const KrakenClient = require('./krakenClient');
const StrategyEngine = require('./strategyEngine');
const SignalEngine = require('./signalEngine');
const RiskManager = require('./riskManager');
const OrderExecutor = require('./orderExecutor');
const SupabaseClient = require('./models/supabaseClient');

class YuriCryptoAgent {
  constructor() {
    this.kraken = new KrakenClient();
    this.strategy = new StrategyEngine();
    this.signal = new SignalEngine();
    this.risk = new RiskManager();
    this.db = new SupabaseClient();
    this.executor = new OrderExecutor(this.kraken, this.db);

    this.recentCandles = [];
    this.openTrade = null;
    this.sessionActive = false;
    this.firstCandleCollecting = false;
    this.running = true;
  }

  async start() {
    console.log('═══════════════════════════════════════');
    console.log(' Yuri Crypto Agent — Starting');
    console.log(`  Pair: ${config.trading.pair}`);
    console.log(`  Strategy: 50 SMA + First Candle Breakout`);
    console.log(`  AI: ${config.ollama.model} @ ${config.ollama.baseUrl}`);
    console.log('═══════════════════════════════════════');

    // Verify Kraken connection
    try {
      const balance = await this.kraken.getBalance();
      const usdBalance = parseFloat(balance.ZCAD || balance.CAD || balance.ZUSD || balance.USD || 0);
      console.log(`[Kraken] Connected — Balance: $${usdBalance.toFixed(2)} CAD`);
      this.risk.setSessionStartBalance(usdBalance);
    } catch (e) {
      console.error(`[Kraken] Connection failed: ${e.message}`);
      await this.executor._sendTelegram(`<b>Yuri Crypto: Kraken connection failed</b>\n${e.message}`);
    }

    // Calculate daily SMA on startup
    await this._calculateDailySMA();

    // Connect WebSocket for live candles
    this.kraken.connectWebSocket((candle) => this._onCandle(candle));

    // Force-activate if starting during market hours
    const now = new Date();
    const etOffset = -4; // EDT
    const etHour = (now.getUTCHours() + etOffset + 24) % 24;
    const etMinute = now.getUTCMinutes();
    
    if (etHour > 9 || (etHour === 9 && etMinute >= 30)) {
      if (etHour < 16) {
        console.log('[Startup] Market is open — force-activating session');
        this.sessionActive = true;
        
        // Fetch first candle (9:30-9:35 AM ET) to lock the range
        try {
          const startTime = new Date(now);
          startTime.setUTCHours(13, 30, 0, 0); // 9:30 AM ET in UTC
          const since = Math.floor(startTime.getTime() / 1000);
          
          const data = await this.kraken.getOHLC(config.trading.krakenPair, 1, since);
          const pair = Object.keys(data).find(k => k !== 'last');
          if (pair && data[pair]) {
            // Take the first 5 minutes of data
            const candles = data[pair].slice(0, 5);
            for (const c of candles) {
              this.strategy.addFirstCandleData({
                time: c[0],
                open: parseFloat(c[1]),
                high: parseFloat(c[2]),
                low: parseFloat(c[3]),
                close: parseFloat(c[4]),
              });
            }
            this.strategy.lockFirstCandle();
          }
        } catch (e) {
          console.error(`[Startup] Failed to fetch first candle: ${e.message}`);
        }
      }
    }

    // Session timer — check every 30 seconds
    this._sessionLoop();

    // Notify startup
    await this.executor._sendTelegram(
      `<b>Yuri Crypto Agent Started</b>\n` +
      `Pair: ${config.trading.pair}\n` +
      `Bias: ${this.strategy.sessionBias}\n` +
      `SMA50: $${(this.strategy.sma50Value || 0).toFixed(2)}\n` +
      `Strategy: 50 SMA + First Candle Breakout`
    );
  }

  async _calculateDailySMA() {
    try {
      const data = await this.kraken.getOHLC(config.trading.krakenPair, 1440);
      const pair = Object.keys(data).find(k => k !== 'last');
      if (pair && data[pair]) {
        this.strategy.calculateSMA50(data[pair]);
      }
    } catch (e) {
      console.error(`[SMA] Failed to calculate: ${e.message}`);
      this.strategy.sessionBias = 'NEUTRAL';
    }
  }

  _onCandle(candle) {
    this.recentCandles.push(candle);
    if (this.recentCandles.length > 100) {
      this.recentCandles = this.recentCandles.slice(-100);
    }

    // Collect first candle data
    if (this.firstCandleCollecting) {
      this.strategy.addFirstCandleData(candle);
    }

    // Evaluate strategy if session is active and first candle is locked
    if (this.sessionActive && this.strategy.firstCandleLocked) {
      this._evaluateCandle(candle);
    }
  }

  async _evaluateCandle(candle) {
    // Check daily loss limit
    try {
      /*
      const balance = await this.kraken.getTradeBalance();
      const equity = parseFloat(balance.e || 0);
      if (this.risk.checkDailyLossLimit(equity)) {
        console.log('[HALT] Daily loss limit reached — stopping trading');
        await this.executor._sendTelegram('<b>Yuri Crypto: HALTED</b>\nDaily loss limit reached. No more trades today.');
        this.sessionActive = false;
        return;
      }
      */
    } catch (e) {
      // Continue if balance check fails
    }

    // Monitor open position
    if (this.openTrade) {
      const exitCheck = this.strategy.checkExit(
        this.openTrade.side,
        this.openTrade.entry_price,
        this.openTrade.stop_loss,
        this.openTrade.take_profit,
        candle.close
      );

      if (exitCheck.exit) {
        try {
          await this.executor.closePosition(this.openTrade, candle.close, exitCheck.reason);
          this.openTrade = null;
        } catch (e) {
          console.error(`[Exit] Failed: ${e.message}`);
        }
      }
      return; // Don't look for new entries while in a position
    }

    // Check max concurrent trades
    if (this.risk.isHalted()) return;

    // Evaluate strategy
    const evaluation = this.strategy.evaluateCandle(candle);
    if (evaluation.signal === 'hold') return;

    // Get AI confirmation
    const aiSignal = await this.signal.getSignal(
      this.strategy.getState(),
      this.recentCandles,
      null
    );

    // Log signal to Supabase
    await this.db.insert('crypto_signals', {
      agent: 'yuri',
      pair: config.trading.pair,
      action: aiSignal.action,
      confidence: aiSignal.confidence,
      reason: aiSignal.reason,
      current_price: candle.close,
      session_bias: this.strategy.sessionBias,
      executed: false,
    });

    console.log(`[AI Signal] ${aiSignal.action} (${(aiSignal.confidence * 100).toFixed(0)}%) — ${aiSignal.reason}`);

    // Check confidence threshold
    if (aiSignal.confidence < config.risk.confidenceThreshold) {
      console.log(`[Skip] Confidence ${aiSignal.confidence} < ${config.risk.confidenceThreshold}`);
      return;
    }

    // Check signal matches strategy
    if (aiSignal.action !== evaluation.signal) {
      console.log(`[Skip] AI says ${aiSignal.action} but strategy says ${evaluation.signal}`);
      return;
    }

    // Execute trade
    try {
      const balance = await this.kraken.getBalance();
      const usdBalance = parseFloat(balance.ZCAD || balance.CAD || balance.ZUSD || balance.USD || 0);

      const stopLoss = this.risk.calculateStopLoss(aiSignal.action, candle.close);
      const takeProfit = this.risk.calculateTakeProfit(aiSignal.action, candle.close);
      const qty = this.risk.calculatePositionSize(usdBalance, candle.close, stopLoss);

      if (qty <= 0) {
        console.log('[Skip] Position size too small');
        return;
      }

      const result = await this.executor.placeOrder(
        aiSignal.action,
        qty,
        candle.close,
        stopLoss,
        takeProfit,
        this.strategy.getState()
      );

      this.openTrade = {
        id: result.tradeId,
        side: aiSignal.action,
        entry_price: candle.close,
        quantity: qty,
        stop_loss: stopLoss,
        take_profit: takeProfit,
      };

      // Update signal as executed
      await this.db.update('crypto_signals', result.tradeId, { executed: true });

    } catch (e) {
      console.error(`[Trade] Execution failed: ${e.message}`);
    }
  }

  async _sessionLoop() {
    while (this.running) {
      const now = new Date();
      const etOffset = -4; // EDT
      const etHour = (now.getUTCHours() + etOffset + 24) % 24;
      const etMinute = now.getUTCMinutes();
      const isWeekday = now.getUTCDay() >= 1 && now.getUTCDay() <= 5;

      // Session open: 9:30 AM ET
      if (isWeekday && etHour === 9 && etMinute === 30 && !this.sessionActive) {
        console.log('[Session] Market open — starting first candle collection');
        this.sessionActive = true;
        this.firstCandleCollecting = true;
        this.strategy.resetSession();
        await this._calculateDailySMA();
      }

      // Lock first candle: 9:35 AM ET
      if (isWeekday && etHour === 9 && etMinute === 35 && this.firstCandleCollecting) {
        this.firstCandleCollecting = false;
        this.strategy.lockFirstCandle();
        console.log('[Session] First candle locked — watching for breakouts');
      }

      // Session close: 4:00 PM ET
      if (etHour === 16 && etMinute === 0 && this.sessionActive) {
        console.log('[Session] Market close — closing positions');
        if (this.openTrade) {
          try {
            const ticker = await this.kraken.getTicker(config.trading.krakenPair);
            const pair = Object.keys(ticker)[0];
            const price = parseFloat(ticker[pair].c[0]);
            await this.executor.closePosition(this.openTrade, price, 'End of session');
            this.openTrade = null;
          } catch (e) {
            console.error(`[Session Close] Failed: ${e.message}`);
          }
        }
        this.sessionActive = false;
        this.risk.setSessionStartBalance(null);
      }

      // Daily SMA recalculation at 9:25 AM ET
      if (isWeekday && etHour === 9 && etMinute === 25) {
        await this._calculateDailySMA();
      }

      await new Promise(r => setTimeout(r, 30000)); // Check every 30 seconds
    }
  }

  stop() {
    this.running = false;
    this.kraken.closeWebSocket();
    console.log('[Yuri] Agent stopped');
  }
}

// ── Start ──
const agent = new YuriCryptoAgent();

process.on('SIGTERM', () => agent.stop());
process.on('SIGINT', () => agent.stop());

agent.start().catch((e) => {
  console.error(`[Fatal] ${e.message}`);
  process.exit(1);
});
