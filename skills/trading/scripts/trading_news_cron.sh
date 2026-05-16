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
ENV_FILE="/home/tonygale/openclaw/.env"
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
/home/tonygale/openclaw/.venv/bin/python /home/tonygale/openclaw/skills/trading/scripts/social_scanner.py truth-social >> "$LOG_FILE" 2>&1 || true
/home/tonygale/openclaw/.venv/bin/python /home/tonygale/openclaw/skills/trading/scripts/news_scanner.py fetch >> "$LOG_FILE" 2>&1 || true

# Alert engine is the decision point.
ALERTS="$(/home/tonygale/openclaw/.venv/bin/python /home/tonygale/openclaw/skills/trading/scripts/alert_engine.py check 2>&1)" || RC=$?
RC=${RC:-0}
echo "$ALERTS" >> "$LOG_FILE"
echo "alert_engine exit=$RC" >> "$LOG_FILE"

if [ "$VERBOSE" -eq 0 ] && echo "$ALERTS" | grep -qx "No alerts triggered."; then
    echo "(quiet: no alerts)" >> "$LOG_FILE"
    echo "" >> "$LOG_FILE"
    exit 0
fi

NARRATIVE="$(ALERTS_VAL="$ALERTS" /home/tonygale/openclaw/.venv/bin/python - <<'PY' 2>>"$LOG_FILE"
import json, os, sys, urllib.request, urllib.error

alerts = os.environ.get("ALERTS_VAL", "")[:6000]
if not alerts.strip():
    sys.exit(0)

prompt = f"""You are summarizing market alerts for Tony, a trader. Given the raw alert dump below,
write a tight impact-focused brief (2-4 sentences, plain text, no markdown).

- Lead with the highest-impact tickers/events
- Note direction (bullish/bearish) and rough magnitude
- Skip duplicate or low-signal items
- Do not invent numbers — only use what's in the alerts

Raw alerts:
{alerts}"""

payload = json.dumps({
    "model": "quick36:latest",
    "prompt": prompt,
    "stream": False,
    "think": False,
    "keep_alive": "10m",
    "options": {"temperature": 0.3, "num_ctx": 8192, "num_predict": 350},
}).encode()

url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434") + "/api/generate"
req = urllib.request.Request(url, data=payload,
                              headers={"Content-Type": "application/json"}, method="POST")
try:
    with urllib.request.urlopen(req, timeout=90) as r:
        print(json.loads(r.read()).get("response", "").strip())
except Exception as e:
    print(f"(narrative unavailable: {type(e).__name__}: {str(e)[:100]})", file=sys.stderr)
PY
)"

HEADER="📰 Market alerts — $(date '+%H:%M %Z')"
if [ -n "$NARRATIVE" ] && [ ${#NARRATIVE} -gt 30 ]; then
    MESSAGE="${HEADER}

${NARRATIVE}

— Raw —
${ALERTS}"
else
    MESSAGE="${HEADER}

${ALERTS}"
fi

if [ ${#MESSAGE} -gt 4000 ]; then
    if [ -n "$NARRATIVE" ] && [ ${#NARRATIVE} -gt 30 ]; then
        BUDGET=$((3800 - ${#HEADER} - ${#NARRATIVE}))
        [ "$BUDGET" -lt 200 ] && BUDGET=200
        ALERTS_TRIM="${ALERTS:0:$BUDGET}"
        MESSAGE="${HEADER}

${NARRATIVE}

— Raw —
${ALERTS_TRIM}
...[full in news.log]"
    else
        MESSAGE="${MESSAGE:0:4000}
...[truncated]"
    fi
fi

curl -sS --max-time 15 \
    -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${MESSAGE}" \
    >> "$LOG_FILE" 2>&1

echo "" >> "$LOG_FILE"
