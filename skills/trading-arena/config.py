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
RE_ENTRY_COOLDOWN_MINUTES = float(os.environ.get("RE_ENTRY_COOLDOWN_MINUTES", "15"))

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
# Kraken fee model. Per-side rates in PERCENT. Defaults below are the
# EMPIRICALLY MEASURED rates for THIS account (not Kraken's generic doc
# tier): taker 0.80%/side (market, measured 2026-05-18, .env-confirmed) and
# maker 0.40%/side (post-only limit, measured 2026-05-18 via a real resting
# XRPUSD round-trip — both legs filled at exactly 0.4000%/side). Both still
# drop with 30-day volume. KRAKEN_ORDER_MODE selects which the executor uses
# and therefore which round-trip the accounting/kill-switch assume.
KRAKEN_TAKER_FEE_PCT = float(os.environ.get("KRAKEN_TAKER_FEE_PCT", "0.80"))   # %/side, market (measured 2026-05-18)
KRAKEN_MAKER_FEE_PCT = float(os.environ.get("KRAKEN_MAKER_FEE_PCT", "0.40"))   # %/side, post-only limit (measured 2026-05-18)
KRAKEN_ORDER_MODE = os.environ.get("KRAKEN_ORDER_MODE", "market").strip().lower()  # market | post_only
# Round-trip fee as a FRACTION of notional (= 2 sides at the active mode's
# rate). paper_trader subtracts this from live pnl at close; trap_catcher uses
# it as a profit hurdle; the LIVE_DAILY_LOSS_LIMIT kill switch reads the netted
# pnl. With measured defaults this resolves to 0.016 (taker, market mode) or
# 0.008 (maker, post_only mode). Backtest 2026-05-17 proved no bot beats even
# the lower maker round-trip — switching to post-only is necessary, not
# sufficient; it does not by itself create an edge.
# An explicit KRAKEN_ROUNDTRIP_FEE_PCT in .env still overrides everything.
_kraken_side_pct = KRAKEN_MAKER_FEE_PCT if KRAKEN_ORDER_MODE == "post_only" else KRAKEN_TAKER_FEE_PCT
KRAKEN_ROUNDTRIP_FEE_PCT = float(
    os.environ.get("KRAKEN_ROUNDTRIP_FEE_PCT", str(round(_kraken_side_pct * 2 / 100, 6)))
)

# Stocks (Questrade) are commission-free; the only round-trip cost is spread/
# slippage. Fee-aware bots use this for stock signals so they don't apply the
# 1.6% Kraken fee floor to stocks. Override via .env STOCK_ROUNDTRIP_FEE_PCT.
STOCK_ROUNDTRIP_FEE_PCT = float(os.environ.get("STOCK_ROUNDTRIP_FEE_PCT", "0.0010"))  # ~0.10% spread proxy


def roundtrip_fee_pct(asset_type: str) -> float:
    """Round-trip cost fraction by asset: Kraken fee for crypto, spread proxy
    for stocks. Crypto path is unchanged from the original constant."""
    return STOCK_ROUNDTRIP_FEE_PCT if asset_type == "stock" else KRAKEN_ROUNDTRIP_FEE_PCT

# === FEE-AWARE PROMOTION GATE ===
# A bot may only place LIVE crypto orders after it has proven a real,
# fee-beating edge in PAPER: >= MIN_PROMOTION_TRADES closed paper trades whose
# mean net-of-fee expectancy is positive at 95% confidence (CI lower bound > 0).
# This is the structural safeguard that would have prevented the 2026-05 live
# bleed — no bot in the arena has ever passed it (backtest 2026-05-17). Enabled
# by default; bypass must be explicit and is surfaced in the trade reason.
PROMOTION_GATE_ENABLED = os.environ.get("PROMOTION_GATE_ENABLED", "true").lower() == "true"
MIN_PROMOTION_TRADES = int(os.environ.get("MIN_PROMOTION_TRADES", "100"))

# === AUTONOMOUS STOCK TRADING (real money on Questrade) ===
# Fully independent of the manual concierge (QUESTRADE_ALLOW_TRADING /
# MANUAL_STOCK_TRADING_ENABLED). Autonomous real orders require ALL of:
#   1. LIVE_STOCK_TRADING_ENABLED=true  (autonomous master / eligibility)
#   2. bot_id in LIVE_STOCK_TRADING_BOTS (per-bot allowlist)
#   3. LIVE_STOCK_ALLOW_ORDERS=true     (validate-mode switch — false ⇒ dry-run)
#   4. US equity market open (hard time gate, enforced in questrade_executor)
# (1)+(2) let the path execute; (3) decides real-POST vs dry-run; (4) is a hard
# off-hours block on real orders. This mirrors the crypto three-gate model
# (LIVE_TRADING_ENABLED / LIVE_TRADING_BOTS / KRAKEN_ALLOW_TRADING) and is
# fully independent of the manual concierge gates.
LIVE_STOCK_TRADING_ENABLED = os.environ.get("LIVE_STOCK_TRADING_ENABLED", "false").lower() == "true"
LIVE_STOCK_ALLOW_ORDERS = os.environ.get("LIVE_STOCK_ALLOW_ORDERS", "false").lower() == "true"
LIVE_STOCK_TRADING_BOTS = [b.strip() for b in os.environ.get("LIVE_STOCK_TRADING_BOTS", "").split(",") if b.strip()]
LIVE_STOCK_MAX_POSITION_USD = float(os.environ.get("LIVE_STOCK_MAX_POSITION_USD", "50.0"))
LIVE_STOCK_MAX_EXPOSURE_USD = float(os.environ.get("LIVE_STOCK_MAX_EXPOSURE_USD", "200.0"))
LIVE_STOCK_DAILY_LOSS_LIMIT = float(os.environ.get("LIVE_STOCK_DAILY_LOSS_LIMIT", "-25.0"))

# === HUMAN-IN-THE-LOOP CONCIERGE (Telegram) ===
MANUAL_TRADING_ENABLED = os.environ.get("MANUAL_TRADING_ENABLED", "false").lower() == "true"
MANUAL_MAX_POSITION_USD = float(os.environ.get("MANUAL_MAX_POSITION_USD", "25.0"))
MANUAL_MAX_EXPOSURE_USD = float(os.environ.get("MANUAL_MAX_EXPOSURE_USD", "50.0"))
MANUAL_DAILY_LOSS_LIMIT = float(os.environ.get("MANUAL_DAILY_LOSS_LIMIT", "-10.0"))
TELEGRAM_TRADER_BOT_TOKEN = os.environ.get("TELEGRAM_TRADER_BOT_TOKEN", "")
# The concierge uses the same chat_id as Yuri (same user, different bot)
