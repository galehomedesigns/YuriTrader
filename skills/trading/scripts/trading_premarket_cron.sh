#!/bin/bash
# Pre-market briefing — 09:00 ET Mon–Fri.
# Runs portfolio/quotes/market-data/alerts/auto-trader status inside the
# container and posts a single Telegram summary. Skips news/social/dashboard
# regen (covered by trading_news_cron.sh every 15 min + trading_dashboard_cron.sh
# at noon).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/../logs"
ENV_FILE="/home/tonygale/openclaw/.env"
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
    output=$(/home/tonygale/openclaw/.venv/bin/python "$@" 2>&1)
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
    run_cmd "Portfolio"    /home/tonygale/openclaw/skills/questrade/scripts/questrade.py portfolio
    run_cmd "Quotes"       /home/tonygale/openclaw/skills/questrade/scripts/questrade.py quote $WATCHLIST
    run_cmd "Market snap"  /home/tonygale/openclaw/skills/trading/scripts/market_data.py snapshot
    run_cmd "Alerts"       /home/tonygale/openclaw/skills/trading/scripts/alert_engine.py check
    run_cmd "Auto-trader"  /home/tonygale/openclaw/skills/trading/scripts/auto_trader.py status
)"

echo "$BRIEFING" >> "$LOG_FILE"

# LLM narrative — quick36 synthesizes the raw briefing into a tactical paragraph
NARRATIVE="$(BRIEFING_VAL="$BRIEFING" /home/tonygale/openclaw/.venv/bin/python - <<'PY' 2>>"$LOG_FILE"
import json, os, sys, urllib.request, urllib.error

briefing = os.environ.get("BRIEFING_VAL", "")[:8000]
if not briefing.strip():
    sys.exit(0)

prompt = f"""You are writing Tony's pre-market briefing for today's open. Given the raw data below,
produce a tight tactical paragraph (4-6 sentences, plain text, no markdown, no headings).

Lead with portfolio P&L direction and any open positions worth flagging.
Mention specific watchlist movers (price + % change), notable alerts, and auto-trader posture.
Skip headers, dollar tables, and section labels — synthesize.
Do not invent numbers; only use what's in the briefing.

Raw briefing:
{briefing}"""

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

HEADER="📈 Pre-Market Briefing — $(date '+%a %b %d, %Y')"
if [ -n "$NARRATIVE" ] && [ ${#NARRATIVE} -gt 50 ]; then
    MESSAGE="${HEADER}

${NARRATIVE}

— Details —
${BRIEFING}
Dashboard: ${DASHBOARD_URL}"
else
    MESSAGE="${HEADER}

${BRIEFING}
Dashboard: ${DASHBOARD_URL}"
fi

if [ ${#MESSAGE} -gt 4000 ]; then
    if [ -n "$NARRATIVE" ] && [ ${#NARRATIVE} -gt 50 ]; then
        BUDGET=$((3700 - ${#HEADER} - ${#NARRATIVE}))
        [ "$BUDGET" -lt 200 ] && BUDGET=200
        BRIEFING_TRIM="${BRIEFING:0:$BUDGET}"
        MESSAGE="${HEADER}

${NARRATIVE}

— Details —
${BRIEFING_TRIM}
...[full in premarket.log]
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
