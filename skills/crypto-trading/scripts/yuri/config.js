/**
 * File: config.js
 * Yuri Crypto Agent — All tunable parameters
 */
module.exports = {
  agent: 'yuri',

  kraken: {
    apiKey: process.env.KRAKEN_API_KEY,
    apiSecret: process.env.KRAKEN_API_SECRET,
    restBase: 'https://api.kraken.com',
    wsUrl: 'wss://ws.kraken.com/v2',
  },

  trading: {
    pair: 'BTC/CAD',
    krakenPair: 'XXBTZCAD',
    wsPair: 'BTC/CAD',
    candleInterval: 1,
    dailyInterval: 1440,
    smaPeriod: 50,
    firstCandleMinutes: 5,
    sessionOpenHour: 9,
    sessionOpenMinute: 30,
    sessionCloseHour: 16,
    timezone: 'America/New_York',
  },

  risk: {
    maxPositionPct: 0.20,
    stopLossPct: 0.015,
    takeProfitPct: 0.03,
    dailyLossLimitPct: 0.15,
    maxConcurrentTrades: 2,
    minOrderUSD: 12,
    confidenceThreshold: 0.75,
  },

  ollama: {
    baseUrl: process.env.OLLAMA_BASE_URL || 'http://100.84.217.85:11434',
    model: 'quick36:latest',
    timeoutMs: 15000,
  },

  supabase: {
    url: process.env.SUPABASE_URL,
    key: process.env.SUPABASE_SERVICE_KEY,
    tables: {
      trades: 'crypto_trades',
      signals: 'crypto_signals',
    },
  },

  telegram: {
    botToken: process.env.TELEGRAM_BOT_TOKEN,
    chatId: process.env.TELEGRAM_CHAT_ID || '6545739863',
  },
};
