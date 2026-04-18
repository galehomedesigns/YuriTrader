#!/bin/bash
# Emergency kill switch for live trading on Kraken.
# - Sets LIVE_TRADING_ENABLED=false in .env
# - Sets KRAKEN_ALLOW_TRADING=false in .env (closes both gates)
# - Calls Kraken CancelAll to cancel all open orders
# - Sends Telegram alert
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

# 1. Disable both gates in .env
echo "Step 1: Closing both safety gates in .env"
sed -i 's/^LIVE_TRADING_ENABLED=.*/LIVE_TRADING_ENABLED=false/' "$ENV_FILE"
sed -i 's/^KRAKEN_ALLOW_TRADING=.*/KRAKEN_ALLOW_TRADING=false/' "$ENV_FILE"
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
echo "To re-enable, edit $ENV_FILE:"
echo "  LIVE_TRADING_ENABLED=true"
echo "  KRAKEN_ALLOW_TRADING=true"
