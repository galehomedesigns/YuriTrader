"""
Configuration for the trading agent.
All keys read from environment variables.
"""
import os

# -- API Keys --
QUESTRADE_REFRESH_TOKEN = os.getenv("QUESTRADE_REFRESH_TOKEN", "")
ALPHA_VANTAGE_KEY       = os.getenv("ALPHA_VANTAGE_KEY", "")  # Free
FINNHUB_KEY             = os.getenv("FINNHUB_KEY", "")          # Free

# -- Supabase --
SUPABASE_URL        = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

# -- Telegram --
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "6545739863")

# -- Questrade Token --
TOKEN_FILE = "/home/tonygale/openclaw/state/questrade_token.json"

# -- Questrade Web Login (for browser automation) --
QUESTRADE_WEB_USER     = os.getenv("QUESTRADE_WEB_USER", "")
QUESTRADE_WEB_PASSWORD = os.getenv("QUESTRADE_WEB_PASSWORD", "")
BROWSER_STATE_DIR      = "/home/tonygale/openclaw/state/questrade_browser_state"
TWO_FA_CODE_FILE       = "/data/.openclaw/questrade_2fa_code.txt"

# -- Risk Controls --
MAX_TRADE_VALUE    = 9.00
MAX_POSITIONS      = 5
MAX_TOTAL_EXPOSURE = 50.00
TRAILING_STOP_PCT  = 0.015   # 1.5% trailing stop — sells when price drops 1.5% from peak
STOP_LOSS_PCT      = -0.02   # -2% hard stop from entry (safety net)
DAILY_LOSS_PAUSE   = -5.00
MAX_HOLD_BARS      = 78      # ~1 trading day at 5-min bars

# -- Buy Flag Minimum --
MIN_FLAGS_TO_BUY = 3

# -- Watchlist --
US_WATCHLIST      = ["AAPL", "MSFT", "NVDA", "TSLA", "SPY"]
TSX_WATCHLIST     = ["RY.TO", "TD.TO", "SHOP.TO"]
INVERSE_WATCHLIST = ["SH", "SDS", "HIU.TO"]  # Inverse ETFs — go up when market goes down

WATCHLIST = US_WATCHLIST + TSX_WATCHLIST + INVERSE_WATCHLIST

# Inverse ETFs use flipped buy logic — bearish signals = buy signals
INVERSE_SYMBOLS = set(INVERSE_WATCHLIST)

# -- Data Settings --
BAR_INTERVAL         = "5min"
NEWS_LOOKBACK_HOURS  = 2
NEWS_HIGH_THRESHOLD  = 0.35

# -- Scheduler --
BUY_INTERVAL_MINUTES  = 15
SELL_INTERVAL_MINUTES = 5
MARKET_OPEN_HOUR      = 9
MARKET_OPEN_MINUTE    = 30
FORCE_SELL_HOUR       = 15
FORCE_SELL_MINUTE     = 55


def twelve_data_symbol(symbol):
    """Convert Questrade symbol format to Twelve Data format.
    Questrade: SHOP.TO  →  Twelve Data: SHOP:TSX
    """
    if symbol.endswith(".TO"):
        return symbol.replace(".TO", ":TSX")
    return symbol


def questrade_symbol(symbol):
    """Convert Twelve Data format back to Questrade format.
    Twelve Data: SHOP:TSX  →  Questrade: SHOP.TO
    """
    if ":TSX" in symbol:
        return symbol.replace(":TSX", ".TO")
    return symbol
