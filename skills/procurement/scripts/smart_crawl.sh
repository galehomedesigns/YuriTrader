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
ENV_FILE="/docker/openclaw-xrt9/.env"
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
python3 "$SCRIPT_DIR/update_status.py" $DRY_RUN 2>&1 | tee -a "$LOG_FILE"

# Step 2: Crawl all active sources
echo "" | tee -a "$LOG_FILE"
echo "--- Step 2: Crawling all active sources ---" | tee -a "$LOG_FILE"
CRAWL_OUTPUT=$(python3 "$SCRIPT_DIR/crawl.py" $DRY_RUN 2>&1)
echo "$CRAWL_OUTPUT" | tee -a "$LOG_FILE"

# Extract summary line for notification
SUMMARY=$(echo "$CRAWL_OUTPUT" | grep -i "^DONE" || echo "Crawl completed")

# Step 3: Get final stats
echo "" | tee -a "$LOG_FILE"
echo "--- Step 3: Final stats ---" | tee -a "$LOG_FILE"
python3 "$SCRIPT_DIR/update_status.py" --stats 2>&1 | tee -a "$LOG_FILE"

echo "" | tee -a "$LOG_FILE"
echo "=== Procurement Crawl Finished: $(date) ===" | tee -a "$LOG_FILE"

# Step 4: Telegram notification (skip in dry-run)
if [ -z "$DRY_RUN" ] && [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    MSG="📋 Procurement crawl complete
$SUMMARY
$(date '+%Y-%m-%d %H:%M ET')"
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="$TELEGRAM_CHAT_ID" \
        -d text="$MSG" \
        -d parse_mode="HTML" \
        -o /dev/null 2>/dev/null || true
fi

# Cleanup: keep only last 30 log files
ls -t "$LOG_DIR"/crawl_*.log 2>/dev/null | tail -n +31 | xargs rm -f 2>/dev/null || true
