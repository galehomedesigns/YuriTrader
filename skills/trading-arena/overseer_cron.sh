#!/bin/bash
# Overseer cron wrapper — runs overseer tools inside the openclaw container.
# Container has httpx + all required python deps; host does not.
#
# Usage:
#   ./overseer_cron.sh game_plan      # Pre-market game plan
#   ./overseer_cron.sh autopsy        # Post-market trade autopsy
#   ./overseer_cron.sh super_prompt   # Weekly super-prompt
#   ./overseer_cron.sh restrictions   # Check/enforce bot restrictions
#   ./overseer_cron.sh analytics      # Performance analytics

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
LOG_FILE="$LOG_DIR/overseer.log"
CONTAINER="openclaw-xrt9-openclaw-1"
OVERSEER_DIR_IN_CONTAINER="/home/tonygale/openclaw/skills/trading-arena/overseer"
ENV_FILE="/home/tonygale/openclaw/.env"

mkdir -p "$LOG_DIR"

if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        export "$key=$value" 2>/dev/null || true
    done < "$ENV_FILE"
fi

ACTION="${1:-}"
if [ -z "$ACTION" ]; then
    echo "Usage: $0 {game_plan|autopsy|super_prompt|restrictions|analytics|tay_analytics}"
    exit 1
fi

echo "=== Overseer $ACTION: $(date) ===" >> "$LOG_FILE"

run_in_container() {
    /home/tonygale/openclaw/.venv/bin/python "$OVERSEER_DIR_IN_CONTAINER/$1" "${@:2}" >> "$LOG_FILE" 2>&1
}

send_to_telegram() {
    # $1 = header, stdin = body. Posts to Telegram if creds are set.
    local HEADER="$1"
    local BODY
    BODY=$(cat)
    [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ] && return 0
    local MSG="${HEADER}

${BODY}"
    if [ ${#MSG} -gt 4000 ]; then
        MSG="${MSG:0:3900}
...[full report in overseer.log]"
    fi
    curl -sS --max-time 15 \
        -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=${MSG}" \
        >> "$LOG_FILE" 2>&1
}

case "$ACTION" in
    game_plan)
        PLAN_OUT="$(/home/tonygale/openclaw/.venv/bin/python "$OVERSEER_DIR_IN_CONTAINER/game_plan.py" 2>&1)"
        echo "$PLAN_OUT" >> "$LOG_FILE"
        echo "$PLAN_OUT" | send_to_telegram "📊 Pre-Market Game Plan — $(date '+%a %b %d')"
        ;;
    autopsy)
        run_in_container autopsy.py
        run_in_container tay_analytics.py --telegram
        ;;
    tay_analytics)
        run_in_container tay_analytics.py --telegram
        ;;
    super_prompt)
        run_in_container super_prompt.py
        ;;
    restrictions)
        run_in_container restrictions.py
        ;;
    analytics)
        run_in_container analytics.py
        ;;
    *)
        echo "Unknown action: $ACTION" >> "$LOG_FILE"
        exit 1
        ;;
esac

echo "" >> "$LOG_FILE"

# Keep log file manageable
tail -5000 "$LOG_FILE" > "$LOG_FILE.tmp" 2>/dev/null && mv "$LOG_FILE.tmp" "$LOG_FILE" || true
