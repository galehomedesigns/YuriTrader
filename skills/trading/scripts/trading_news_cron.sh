#!/bin/bash
# Trading news scan — runs social + news + alert-engine inside the container.
# Silent unless alert_engine.py returns alerts; posts alerts to Telegram otherwise.
# Scheduled every 15 min by root crontab.
#
# Usage:
#   ./trading_news_cron.sh            # normal run, silent on no alerts
#   ./trading_news_cron.sh --verbose  # always post summary (testing)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/../logs"
ENV_FILE="/docker/openclaw-xrt9/.env"
LOG_FILE="$LOG_DIR/news.log"
CONTAINER="openclaw-xrt9-openclaw-1"

mkdir -p "$LOG_DIR"

if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        export "$key=$value" 2>/dev/null || true
    done < "$ENV_FILE"
fi

: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN not set}"
: "${TELEGRAM_CHAT_ID:?TELEGRAM_CHAT_ID not set}"

VERBOSE=0
[ "${1:-}" = "--verbose" ] && VERBOSE=1

echo "=== News scan: $(date -Iseconds) ===" >> "$LOG_FILE"

# Scanners run silently (fill DB). Only their stderr/errors reach the log.
docker exec "$CONTAINER" python3 /home/tonygale/openclaw/skills/trading/scripts/social_scanner.py truth-social >> "$LOG_FILE" 2>&1 || true
docker exec "$CONTAINER" python3 /home/tonygale/openclaw/skills/trading/scripts/news_scanner.py fetch >> "$LOG_FILE" 2>&1 || true

# Alert engine is the decision point.
ALERTS="$(docker exec "$CONTAINER" python3 /home/tonygale/openclaw/skills/trading/scripts/alert_engine.py check 2>&1)" || RC=$?
RC=${RC:-0}
echo "$ALERTS" >> "$LOG_FILE"
echo "alert_engine exit=$RC" >> "$LOG_FILE"

if [ "$VERBOSE" -eq 0 ] && echo "$ALERTS" | grep -qx "No alerts triggered."; then
    echo "(quiet: no alerts)" >> "$LOG_FILE"
    echo "" >> "$LOG_FILE"
    exit 0
fi

MESSAGE="Market alerts ($(date +%H:%M\ %Z))

${ALERTS}"
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
