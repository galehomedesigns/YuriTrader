#!/bin/bash
# Pre-market briefing — 09:00 ET Mon–Fri.
# Runs portfolio/quotes/market-data/alerts/auto-trader status inside the
# container and posts a single Telegram summary. Skips news/social/dashboard
# regen (covered by trading_news_cron.sh every 15 min + trading_dashboard_cron.sh
# at noon).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/../logs"
ENV_FILE="/docker/openclaw-xrt9/.env"
LOG_FILE="$LOG_DIR/premarket.log"
CONTAINER="openclaw-xrt9-openclaw-1"
DASHBOARD_URL="https://187-77-193-40.sslip.io/trading.html"
WATCHLIST="AAPL MSFT NVDA TSLA ENB.TO TD.TO SHOP.TO SPY QQQ"

mkdir -p "$LOG_DIR"

if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        export "$key=$value" 2>/dev/null || true
    done < "$ENV_FILE"
fi

: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN not set}"
: "${TELEGRAM_CHAT_ID:?TELEGRAM_CHAT_ID not set}"

echo "=== Premarket briefing: $(date -Iseconds) ===" >> "$LOG_FILE"

run_cmd() {
    local label="$1"
    shift
    local output rc cleaned
    output=$(docker exec "$CONTAINER" python3 "$@" 2>&1)
    rc=$?
    # Strip Python traceback body: stop at first "Traceback" line; cap at 15 lines.
    # Strip Python traceback body lines (File "...", indented continuations,
    # caret pointers, bare "Traceback" lines); cap at 15 lines.
    cleaned=$(echo "$output" | awk '
        /^  File "/ { next }
        /^    / { next }
        /^  [~^]/ { next }
        /^Traceback \(/ { next }
        { print }
    ' | head -n 15)
    if [ "$rc" -eq 0 ]; then
        printf -- "--- %s ---\n%s\n\n" "$label" "$cleaned"
    else
        printf -- "--- %s (ERROR) ---\n%s\n\n" "$label" "$cleaned"
    fi
}

BRIEFING="$(
    run_cmd "Portfolio"    /data/skills/questrade/scripts/questrade.py portfolio
    run_cmd "Quotes"       /data/skills/questrade/scripts/questrade.py quote $WATCHLIST
    run_cmd "Market snap"  /data/skills/trading/scripts/market_data.py snapshot
    run_cmd "Alerts"       /data/skills/trading/scripts/alert_engine.py check
    run_cmd "Auto-trader"  /data/skills/trading/scripts/auto_trader.py status
)"

echo "$BRIEFING" >> "$LOG_FILE"

MESSAGE="Pre-Market Briefing ($(date +%Y-%m-%d))

${BRIEFING}
Dashboard: ${DASHBOARD_URL}"

if [ ${#MESSAGE} -gt 4000 ]; then
    MESSAGE="${MESSAGE:0:4000}
...[truncated]"
fi

curl -sS --max-time 15 \
    -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${MESSAGE}" \
    >> "$LOG_FILE" 2>&1

echo "" >> "$LOG_FILE"
