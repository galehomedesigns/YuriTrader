#!/bin/bash
# Post-market summary — 16:30 ET Mon–Fri.
# Runs portfolio/market-snap/alerts/auto-trader status+history inside the
# container and posts a single Telegram summary. Skips dashboard regen
# (trading_dashboard_cron.sh handles that at noon).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/../logs"
ENV_FILE="/home/tonygale/openclaw/.env"
LOG_FILE="$LOG_DIR/postmarket.log"
CONTAINER="openclaw-xrt9-openclaw-1"
DASHBOARD_URL="https://187-77-193-40.sslip.io/trading.html"

mkdir -p "$LOG_DIR"

if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        export "$key=$value" 2>/dev/null || true
    done < "$ENV_FILE"
fi

: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN not set}"
: "${TELEGRAM_CHAT_ID:?TELEGRAM_CHAT_ID not set}"

echo "=== Postmarket summary: $(date -Iseconds) ===" >> "$LOG_FILE"

run_cmd() {
    local label="$1"
    shift
    local output rc cleaned
    output=$(/home/tonygale/openclaw/.venv/bin/python "$@" 2>&1)
    rc=$?
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

SUMMARY="$(
    run_cmd "Portfolio"     /home/tonygale/openclaw/skills/questrade/scripts/questrade.py portfolio
    run_cmd "Market snap"   /home/tonygale/openclaw/skills/trading/scripts/market_data.py snapshot
    run_cmd "Alerts"        /home/tonygale/openclaw/skills/trading/scripts/alert_engine.py check
    run_cmd "Auto-trader"   /home/tonygale/openclaw/skills/trading/scripts/auto_trader.py status
    run_cmd "Trade history" /home/tonygale/openclaw/skills/trading/scripts/auto_trader.py history 1
)"

echo "$SUMMARY" >> "$LOG_FILE"

# LLM narrative — quick36 synthesizes the day's data into a post-mortem paragraph
NARRATIVE="$(SUMMARY_VAL="$SUMMARY" /home/tonygale/openclaw/.venv/bin/python - <<'PY' 2>>"$LOG_FILE"
import json, os, sys, urllib.request, urllib.error

summary = os.environ.get("SUMMARY_VAL", "")[:8000]
if not summary.strip():
    sys.exit(0)

prompt = f"""You are writing Tony's post-market wrap-up. Given the raw end-of-day data below,
produce a tight narrative (4-6 sentences, plain text, no markdown, no headings).

Lead with portfolio change today (up/down/flat), then call out specific gainers/losers in the watchlist,
any trades that closed today (with P&L), open positions left overnight, and auto-trader status.
Skip headers, dollar tables, and section labels — synthesize.
Do not invent numbers; only use what's in the summary.

Raw summary:
{summary}"""

payload = json.dumps({
    "model": "quick36:latest",
    "prompt": prompt,
    "stream": False,
    "think": False,
    "keep_alive": "10m",
    "options": {"temperature": 0.4, "num_ctx": 16384, "num_predict": 500},
}).encode()

url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434") + "/api/generate"
req = urllib.request.Request(url, data=payload,
                              headers={"Content-Type": "application/json"}, method="POST")
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        text = json.loads(r.read()).get("response", "").strip()
        print(text)
except Exception as e:
    print(f"(narrative unavailable: {type(e).__name__}: {str(e)[:100]})", file=sys.stderr)
PY
)"

HEADER="📉 Post-Market Summary — $(date '+%a %b %d, %Y')"
if [ -n "$NARRATIVE" ] && [ ${#NARRATIVE} -gt 50 ]; then
    MESSAGE="${HEADER}

${NARRATIVE}

— Details —
${SUMMARY}
Dashboard: ${DASHBOARD_URL}"
else
    MESSAGE="${HEADER}

${SUMMARY}
Dashboard: ${DASHBOARD_URL}"
fi

if [ ${#MESSAGE} -gt 4000 ]; then
    if [ -n "$NARRATIVE" ] && [ ${#NARRATIVE} -gt 50 ]; then
        BUDGET=$((3700 - ${#HEADER} - ${#NARRATIVE}))
        [ "$BUDGET" -lt 200 ] && BUDGET=200
        SUMMARY_TRIM="${SUMMARY:0:$BUDGET}"
        MESSAGE="${HEADER}

${NARRATIVE}

— Details —
${SUMMARY_TRIM}
...[full in postmarket.log]
Dashboard: ${DASHBOARD_URL}"
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
