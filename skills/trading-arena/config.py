"""Global configuration for the Trading Arena."""
import os

# Supabase
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# Market data APIs
TWELVEDATA_KEY = os.environ.get("TWELVEDATA_API_KEY", "")
FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "")
ALPHAVANTAGE_KEY = os.environ.get("ALPHAVANTAGE_KEY", "")

# Kraken (crypto)
KRAKEN_KEY = os.environ.get("KRAKEN_API_KEY", "")
KRAKEN_SECRET = os.environ.get("KRAKEN_API_SECRET", "")

# Ollama AI validation
OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = "quick"

# Telegram
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6545739863")

# TradingView webhook
TV_WEBHOOK_URL = "http://127.0.0.1:8089/webhook"
TV_WEBHOOK_SECRET = os.environ.get("TRADINGVIEW_WEBHOOK_SECRET", "yuri-tv-2026")

# Scan universe
STOCK_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META", "AMD",
    "SPY", "QQQ", "SHOP.TO", "TD.TO", "RY.TO", "ENB.TO",
]
CRYPTO_SYMBOLS = [
    "BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD", "DOGE/USD",
]
KRAKEN_PAIRS = {
    "BTC/USD": "XXBTZUSD", "ETH/USD": "XETHZUSD", "SOL/USD": "SOLUSD",
    "XRP/USD": "XXRPZUSD", "ADA/USD": "ADAUSD", "DOGE/USD": "XDGUSD",
}

# Risk management
PAPER_TRADING = True
STARTING_BALANCE = 1000.0
MAX_POSITION_PCT = 0.05       # 5% per trade
MAX_POSITION_USD = 50.0       # $50 max per trade
MAX_CONCURRENT_POS = 3        # per bot
BOT_DAILY_LOSS_LIMIT = -30.0  # -3% of $1000
GLOBAL_DAILY_LOSS_LIMIT = -500.0  # -5% of $10,000
GLOBAL_MAX_POSITIONS = 20     # across all bots
SCAN_INTERVAL = 60            # seconds between scans

# === LIVE TRADING (real money on Kraken) ===
# Three gates control real-money flow:
#   1. KRAKEN_ALLOW_TRADING=true (server-side gate, used by kraken_executor)
#   2. LIVE_TRADING_ENABLED=true (autonomous arena bots)
#   3. MANUAL_TRADING_ENABLED=true (human-in-the-loop Telegram concierge)
# (1) is required for either (2) or (3). (2) and (3) are independent.
LIVE_TRADING_ENABLED = os.environ.get("LIVE_TRADING_ENABLED", "false").lower() == "true"
LIVE_TRADING_BOTS = [b.strip() for b in os.environ.get("LIVE_TRADING_BOTS", "").split(",") if b.strip()]
LIVE_MAX_POSITION_USD = float(os.environ.get("LIVE_MAX_POSITION_USD", "5.0"))
LIVE_MAX_EXPOSURE_USD = float(os.environ.get("LIVE_MAX_EXPOSURE_USD", "10.0"))
LIVE_DAILY_LOSS_LIMIT = float(os.environ.get("LIVE_DAILY_LOSS_LIMIT", "-3.0"))

# === HUMAN-IN-THE-LOOP CONCIERGE (Telegram) ===
MANUAL_TRADING_ENABLED = os.environ.get("MANUAL_TRADING_ENABLED", "false").lower() == "true"
MANUAL_MAX_POSITION_USD = float(os.environ.get("MANUAL_MAX_POSITION_USD", "25.0"))
MANUAL_MAX_EXPOSURE_USD = float(os.environ.get("MANUAL_MAX_EXPOSURE_USD", "50.0"))
MANUAL_DAILY_LOSS_LIMIT = float(os.environ.get("MANUAL_DAILY_LOSS_LIMIT", "-10.0"))
TELEGRAM_TRADER_BOT_TOKEN = os.environ.get("TELEGRAM_TRADER_BOT_TOKEN", "")
# The concierge uses the same chat_id as Yuri (same user, different bot)
