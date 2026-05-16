#!/bin/bash
# Smart procurement crawl — runs every 2 days via system cron
# Zero LLM usage. Pure Firecrawl scraping + regex parsing + Supabase storage.
#
# Usage:
#   ./smart_crawl.sh          # Full run: clean expired → crawl → log → notify
#   ./smart_crawl.sh --dry-run  # Parse only, don't write to DB

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/../logs"
ENV_FILE="/home/tonygale/openclaw/.env"
LOG_FILE="$LOG_DIR/crawl_$(date +%Y-%m-%d_%H%M).log"

mkdir -p "$LOG_DIR"

# Load environment variables (only well-formed KEY=VALUE lines, skip problematic ones)
if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key value; do
        # Skip comments, blank lines, and keys with spaces in values that aren't quoted
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        export "$key=$value" 2>/dev/null || true
    done < "$ENV_FILE"
else
    echo "ERROR: $ENV_FILE not found" | tee -a "$LOG_FILE"
    exit 1
fi

DRY_RUN=""
if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN="--dry-run"
    echo "=== DRY RUN MODE ===" | tee -a "$LOG_FILE"
fi

echo "=== Procurement Crawl Started: $(date) ===" | tee -a "$LOG_FILE"

# Step 1: Clean expired tenders
echo "" | tee -a "$LOG_FILE"
echo "--- Step 1: Closing expired tenders ---" | tee -a "$LOG_FILE"
/home/tonygale/openclaw/.venv/bin/python "$SCRIPT_DIR/update_status.py" $DRY_RUN 2>&1 | tee -a "$LOG_FILE"

# Step 2: Crawl all active sources
echo "" | tee -a "$LOG_FILE"
echo "--- Step 2: Crawling all active sources ---" | tee -a "$LOG_FILE"
CRAWL_OUTPUT=$(/home/tonygale/openclaw/.venv/bin/python "$SCRIPT_DIR/crawl.py" $DRY_RUN 2>&1)
echo "$CRAWL_OUTPUT" | tee -a "$LOG_FILE"

# Extract summary line for notification
SUMMARY=$(echo "$CRAWL_OUTPUT" | grep -i "^DONE" || echo "Crawl completed")

# Step 3: Get final stats
echo "" | tee -a "$LOG_FILE"
echo "--- Step 3: Final stats ---" | tee -a "$LOG_FILE"
/home/tonygale/openclaw/.venv/bin/python "$SCRIPT_DIR/update_status.py" --stats 2>&1 | tee -a "$LOG_FILE"

echo "" | tee -a "$LOG_FILE"
echo "=== Procurement Crawl Finished: $(date) ===" | tee -a "$LOG_FILE"

# Step 4: LLM digest — quality:latest reviews the latest tenders for Tony's business fit
DIGEST=""
if [ -z "$DRY_RUN" ]; then
    DIGEST=$(/home/tonygale/openclaw/.venv/bin/python - <<'PY' 2>>"$LOG_FILE"
import json, os, sys, urllib.request, urllib.error

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
OLLAMA_URL  = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

if not SUPABASE_URL or not SUPABASE_KEY:
    sys.exit(0)

# Pull last 20 open tenders (newest first)
url = (f"{SUPABASE_URL}/rest/v1/tenders?status=eq.open"
       "&select=title,organization,location,closing_date,category,province,url,raw_text"
       "&order=created_at.desc&limit=20")
req = urllib.request.Request(url, headers={
    "apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"})
try:
    tenders = json.loads(urllib.request.urlopen(req, timeout=20).read())
except Exception as e:
    print(f"(supabase fetch failed: {e})", file=sys.stderr)
    sys.exit(0)
if not tenders:
    sys.exit(0)

lines = []
for t in tenders[:20]:
    title = (t.get("title") or "")[:120]
    org   = (t.get("organization") or "")[:60]
    prov  = t.get("province") or "?"
    cat   = (t.get("category") or "?")[:40]
    close = (t.get("closing_date") or "")[:10] or "?"
    lines.append(f"- [{prov}/{cat}] {title} | {org} | closes {close}")

prompt = f"""You are reviewing today's procurement crawl for Tony Gale of Decades Developments,
a BC-based construction/development firm focused on residential and small-commercial
project management, drafting, and consulting.

From the latest 20 open tenders below, write a SHORT digest (3-5 sentences, plain text, no markdown):
- Lead with 1-3 specific tenders that look like the best fit for Tony's business (cite by title/org)
- Note any thematic clusters (e.g. multiple BC municipal road tenders, healthcare facility builds)
- Flag any closing within 7 days
- Skip the long tail of irrelevant tenders

Tenders:
{chr(10).join(lines)}"""

payload = json.dumps({
    "model": "quality:latest",
    "prompt": prompt,
    "stream": False,
    "think": False,
    "keep_alive": "5m",
    "options": {"temperature": 0.4, "num_ctx": 16384, "num_predict": 500},
}).encode()

req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", data=payload,
                              headers={"Content-Type": "application/json"}, method="POST")
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        print(json.loads(r.read()).get("response", "").strip())
except Exception as e:
    print(f"(digest unavailable: {type(e).__name__}: {str(e)[:100]})", file=sys.stderr)
PY
)
fi

# Step 5: Telegram notification (skip in dry-run)
if [ -z "$DRY_RUN" ] && [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    if [ -n "$DIGEST" ] && [ ${#DIGEST} -gt 30 ]; then
        MSG="📋 Procurement crawl complete
$SUMMARY

— Today's tender fit (quality model) —
$DIGEST

$(date '+%Y-%m-%d %H:%M ET')"
    else
        MSG="📋 Procurement crawl complete
$SUMMARY
$(date '+%Y-%m-%d %H:%M ET')"
    fi
    if [ ${#MSG} -gt 4000 ]; then
        MSG="${MSG:0:4000}
...[truncated]"
    fi
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=$TELEGRAM_CHAT_ID" \
        --data-urlencode "text=$MSG" \
        -o /dev/null 2>/dev/null || true
fi

# Cleanup: keep only last 30 log files
ls -t "$LOG_DIR"/crawl_*.log 2>/dev/null | tail -n +31 | xargs rm -f 2>/dev/null || true
