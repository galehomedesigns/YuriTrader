#!/bin/bash
# Medic cron wrapper — runs medic.py inside the openclaw container and posts the
# report to Telegram. Bypasses the OpenClaw agent/approval system.
#
# Usage:
#   ./medic_cron.sh              # report + dashboard, post to Telegram
#   ./medic_cron.sh report-only  # skip dashboard regeneration

set -euo pipefail

# systemctl --user needs XDG_RUNTIME_DIR to reach the per-user bus. Cron runs
# in a minimal env where this is unset, which made the systemd.* medic checks
# return FAIL even while the services were active. Source of the 2026-04-22
# false-alarm.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

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

# LLM narrative synthesis: pipe the raw checklist through quick36 for a
# human-readable 3-5 sentence digest that leads on failures/time-sensitive items.
NARRATIVE="$(REPORT_VAL="$REPORT" /home/tonygale/openclaw/.venv/bin/python - <<'PY' 2>>"$LOG_FILE"
import json, os, sys, urllib.request, urllib.error

report = os.environ.get("REPORT_VAL", "")[:6000]
if not report.strip():
    sys.exit(0)

prompt = f"""You are summarizing a system health check report for Tony's openclaw stack.

Write a SHORT narrative (3-5 sentences max) that:
- Leads with whether everything is OK or what is failing
- Names specific failures and warnings concisely
- Calls out any time-sensitive items (e.g. "Questrade token expires in N minutes")
- Skips obvious context — Tony knows what each check measures

Do NOT repeat the full checklist. Just synthesize. Plain text, no markdown, no preamble.

Report:
{report}"""

payload = json.dumps({
    "model": "quick36:latest",
    "prompt": prompt,
    "stream": False,
    "think": False,  # qwen3.6 is thinking-capable; skip CoT for terse narrative
    "keep_alive": "10m",
    "options": {"temperature": 0.3, "num_ctx": 8192, "num_predict": 400},
}).encode()

url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434") + "/api/generate"
req = urllib.request.Request(url, data=payload,
                              headers={"Content-Type": "application/json"}, method="POST")
try:
    with urllib.request.urlopen(req, timeout=90) as r:
        text = json.loads(r.read()).get("response", "").strip()
        print(text)
except Exception as e:
    print(f"(narrative unavailable: {type(e).__name__}: {str(e)[:100]})", file=sys.stderr)
PY
)"

HEADER="${PREFIX}📋 Medic — $(date '+%Y-%m-%d %H:%M %Z')"
if [ -n "$NARRATIVE" ] && [ ${#NARRATIVE} -gt 30 ]; then
    MESSAGE="${HEADER}

${NARRATIVE}

— Details —
${REPORT}"
else
    MESSAGE="${HEADER}

${REPORT}"
fi

# Telegram message limit is 4096 chars — preserve narrative, truncate details
if [ ${#MESSAGE} -gt 4000 ]; then
    if [ -n "$NARRATIVE" ] && [ ${#NARRATIVE} -gt 30 ]; then
        # Keep header + narrative, trim details
        BUDGET=$((3800 - ${#HEADER} - ${#NARRATIVE}))
        [ "$BUDGET" -lt 200 ] && BUDGET=200
        REPORT_TRIM="${REPORT:0:$BUDGET}"
        MESSAGE="${HEADER}

${NARRATIVE}

— Details —
${REPORT_TRIM}
...[full report in cron.log]"
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
