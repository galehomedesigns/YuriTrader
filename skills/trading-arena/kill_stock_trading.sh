#!/bin/bash
# Emergency kill switch for AUTONOMOUS stock trading on Questrade.
# - Sets LIVE_STOCK_TRADING_ENABLED=false in .env
# - Sets LIVE_STOCK_ALLOW_ORDERS=false in .env (closes the autonomous gates)
# - Cancels ALL open Questrade orders (safety: clears strays too)
# - Sends Telegram alert
#
# NOTE: This deliberately does NOT touch QUESTRADE_ALLOW_TRADING or
# MANUAL_STOCK_TRADING_ENABLED — the human-in-the-loop Telegram concierge is
# a separate path and stays usable. Use kill_live_trading.sh to stop EVERYTHING.

set -euo pipefail

ENV_FILE="/home/tonygale/openclaw/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found" >&2
    exit 1
fi

while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" =~ ^# ]] && continue
    export "$key=$value" 2>/dev/null || true
done < "$ENV_FILE"

echo "=== STOCK KILL SWITCH ACTIVATED: $(date) ==="

echo "Step 1: Closing autonomous stock gates in .env"
sed -i 's/^LIVE_STOCK_TRADING_ENABLED=.*/LIVE_STOCK_TRADING_ENABLED=false/' "$ENV_FILE"
sed -i 's/^LIVE_STOCK_ALLOW_ORDERS=.*/LIVE_STOCK_ALLOW_ORDERS=false/' "$ENV_FILE"
echo "  Done."

echo "Step 2: Cancelling all open Questrade orders"
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
    print(f"  ERROR cancelling orders: {e}", file=sys.stderr)
    sys.exit(2)
PYEOF

echo "Step 3: Sending Telegram alert"
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="$TELEGRAM_CHAT_ID" \
        -d text="🛑 AUTONOMOUS STOCK TRADING KILLED — manual stop. Stock gates closed, all Questrade orders cancelled. $(date '+%Y-%m-%d %H:%M ET')" \
        -d parse_mode="HTML" \
        -o /dev/null && echo "  Telegram sent" || echo "  Telegram failed"
else
    echo "  TELEGRAM not configured, skipping alert"
fi

echo "=== STOCK KILL SWITCH COMPLETE ==="
echo ""
echo "To re-enable autonomous stock trading, edit $ENV_FILE:"
echo "  LIVE_STOCK_TRADING_ENABLED=true"
echo "  LIVE_STOCK_ALLOW_ORDERS=true   (real orders; leave false for dry-run)"
