#!/bin/bash
# Receipt-processor cron wrapper — runs process_receipts.py inside the openclaw
# container and posts a Telegram summary when new receipts were processed or an
# error occurred. Silent on "nothing to do" runs to avoid daily noise.
#
# Usage:
#   ./receipts_cron.sh              # normal run
#   ./receipts_cron.sh --dry-run    # show what would be processed
#   ./receipts_cron.sh --verbose    # always post (even for no-op runs)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/../logs"
ENV_FILE="/docker/openclaw-xrt9/.env"
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

ARGS=("$@")
VERBOSE=0
DOCKER_ARGS=()
for a in "${ARGS[@]}"; do
    if [ "$a" = "--verbose" ]; then
        VERBOSE=1
    else
        DOCKER_ARGS+=("$a")
    fi
done

echo "=== Receipts ${DOCKER_ARGS[*]:-normal}: $(date -Iseconds) ===" >> "$LOG_FILE"

OUTPUT="$(docker exec "$CONTAINER" python3 /home/tonygale/openclaw/skills/receipts/scripts/process_receipts.py "${DOCKER_ARGS[@]}" 2>&1)" || RC=$?
RC=${RC:-0}
echo "$OUTPUT" >> "$LOG_FILE"
echo "exit=$RC" >> "$LOG_FILE"

# Skip Telegram when nothing happened (and not --verbose, not an error)
if [ "$VERBOSE" -eq 0 ] && [ "$RC" -eq 0 ] && echo "$OUTPUT" | grep -qx "No new receipts to process."; then
    echo "(quiet: nothing processed)" >> "$LOG_FILE"
    echo "" >> "$LOG_FILE"
    exit 0
fi

PREFIX=""
if [ "$RC" -ne 0 ] || echo "$OUTPUT" | grep -qE "^Error|Gemini API error"; then
    PREFIX="ALERT: "
fi

MESSAGE="${PREFIX}Receipts run ($(date +%Y-%m-%d))

${OUTPUT}"
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
