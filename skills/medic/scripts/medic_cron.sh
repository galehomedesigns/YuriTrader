#!/bin/bash
# Medic cron wrapper — runs medic.py inside the openclaw container and posts the
# report to Telegram. Bypasses the OpenClaw agent/approval system.
#
# Usage:
#   ./medic_cron.sh              # report + dashboard, post to Telegram
#   ./medic_cron.sh report-only  # skip dashboard regeneration

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/../logs"
ENV_FILE="/home/tonygale/openclaw/.env"
LOG_FILE="$LOG_DIR/cron.log"
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

MODE="${1:-full}"

echo "=== Medic $MODE: $(date -Iseconds) ===" >> "$LOG_FILE"

REPORT="$(/home/tonygale/openclaw/.venv/bin/python /home/tonygale/openclaw/skills/medic/scripts/medic.py report 2>&1)"
echo "$REPORT" >> "$LOG_FILE"

if [ "$MODE" != "report-only" ]; then
    /home/tonygale/openclaw/.venv/bin/python /home/tonygale/openclaw/skills/medic/scripts/medic.py dashboard >> "$LOG_FILE" 2>&1 || true
fi

PREFIX=""
if echo "$REPORT" | grep -q "\[FAIL\]\|FAIL:"; then
    PREFIX="ALERT: "
fi

MESSAGE="${PREFIX}${REPORT}"
# Telegram message limit is 4096 chars
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
