#!/bin/bash
# Emergency kill switch for ALL live trading (crypto + autonomous stock).
# - Sets LIVE_TRADING_ENABLED=false + KRAKEN_ALLOW_TRADING=false in .env
# - Sets LIVE_STOCK_TRADING_ENABLED=false + LIVE_STOCK_ALLOW_ORDERS=false in .env
# - Cancels all open orders on Kraken AND Questrade
# - Sends Telegram alert
# (The manual Telegram concierge gates are intentionally left alone.)
#
# Use this if anything looks wrong. Better to kill and investigate than risk
# more trades while debugging.

set -euo pipefail

ENV_FILE="/home/tonygale/openclaw/.env"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found" >&2
    exit 1
fi

# Load env vars (skip lines with spaces in values)
while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" =~ ^# ]] && continue
    export "$key=$value" 2>/dev/null || true
done < "$ENV_FILE"

echo "=== KILL SWITCH ACTIVATED: $(date) ==="

# 1. Disable all autonomous gates in .env (crypto + stock)
echo "Step 1: Closing all autonomous safety gates in .env"
sed -i 's/^LIVE_TRADING_ENABLED=.*/LIVE_TRADING_ENABLED=false/' "$ENV_FILE"
sed -i 's/^KRAKEN_ALLOW_TRADING=.*/KRAKEN_ALLOW_TRADING=false/' "$ENV_FILE"
sed -i 's/^LIVE_STOCK_TRADING_ENABLED=.*/LIVE_STOCK_TRADING_ENABLED=false/' "$ENV_FILE"
sed -i 's/^LIVE_STOCK_ALLOW_ORDERS=.*/LIVE_STOCK_ALLOW_ORDERS=false/' "$ENV_FILE"
echo "  Done."

# 2. Cancel all open orders on Kraken
echo "Step 2: Cancelling all open Kraken orders"
/home/tonygale/openclaw/.venv/bin/python - <<'PYEOF'
import sys
sys.path.insert(0, '/home/tonygale/openclaw/skills/trading-arena')
try:
    from shared.kraken_executor import KrakenExecutor
    executor = KrakenExecutor()
    open_orders = executor.get_open_orders()
    if not open_orders:
        print("  No open orders to cancel")
    else:
        result = executor.cancel_all()
        print(f"  Cancelled: {result}")
except Exception as e:
    print(f"  ERROR cancelling orders: {e}", file=sys.stderr)
    sys.exit(2)
PYEOF

# 2b. Cancel all open orders on Questrade (autonomous stock book)
echo "Step 2b: Cancelling all open Questrade orders"
/home/tonygale/openclaw/.venv/bin/python - <<'PYEOF'
import sys
sys.path.insert(0, '/home/tonygale/openclaw/skills/trading-arena')
try:
    from shared.questrade_executor import QuestradeExecutor
    executor = QuestradeExecutor()
    open_orders = executor.get_open_orders()
    if not open_orders:
        print("  No open orders to cancel")
    else:
        result = executor.cancel_all()
        print(f"  Cancelled: {result}")
except Exception as e:
    print(f"  ERROR cancelling Questrade orders: {e}", file=sys.stderr)
    # Non-fatal: Kraken kill already succeeded; surface but don't abort.
PYEOF

# 3. Send Telegram alert
echo "Step 3: Sending Telegram alert"
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="$TELEGRAM_CHAT_ID" \
        -d text="🛑 LIVE TRADING KILLED — manual stop. Both gates closed, all Kraken orders cancelled. $(date '+%Y-%m-%d %H:%M ET')" \
        -d parse_mode="HTML" \
        -o /dev/null && echo "  Telegram sent" || echo "  Telegram failed"
else
    echo "  TELEGRAM not configured, skipping alert"
fi

echo "=== KILL SWITCH COMPLETE ==="
echo ""
echo "To re-enable crypto, edit $ENV_FILE:"
echo "  LIVE_TRADING_ENABLED=true"
echo "  KRAKEN_ALLOW_TRADING=true"
echo "To re-enable autonomous stock:"
echo "  LIVE_STOCK_TRADING_ENABLED=true"
echo "  LIVE_STOCK_ALLOW_ORDERS=true   (real orders; leave false for dry-run)"
