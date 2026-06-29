#!/bin/bash
# Auto-run the price x gap x coil robustness sweep once the low-priced IBKR
# backfill finishes. Detached/idempotent. Telegram-pings the result if creds load.
set -uo pipefail
ROOT=/home/tonygale/openclaw
PY="$ROOT/.venv/bin/python"
OUT=/tmp/opening_sweep_full.log

if [ -f "$ROOT/.env" ]; then
  while IFS='=' read -r k v; do [[ -z "$k" || "$k" =~ ^# ]] && continue; export "$k=$v" 2>/dev/null || true; done < "$ROOT/.env"
fi

echo "[sweep-kickoff] $(date -Iseconds) waiting for low-priced backfill..." >> "$OUT"
while pgrep -f 'ibkr_history/backfill.py' >/dev/null 2>&1; do sleep 60; done
echo "[sweep-kickoff] $(date -Iseconds) backfill ended; running full sweep" >> "$OUT"

OPENING_SWEEP_CACHE_DIRS=backtest_cache_ibkr_tech OPENING_BT_SLIP_CENTS=0.02 \
  "$PY" "$ROOT/skills/trading-arena/ibkr_history/opening_sweep.py" >> "$OUT" 2>&1
echo "[sweep-kickoff] $(date -Iseconds) SWEEP_DONE" >> "$OUT"

# best-effort Telegram ping with the price-range table
TOK="${TELEGRAM_BOT_TOKEN:-}"; CHAT="${TELEGRAM_CHAT_ID:-}"
if [ -n "$TOK" ] && [ -n "$CHAT" ]; then
  MSG=$(printf 'Power-Open sweep done (price x gap x coil, realistic slippage).\n%s' \
        "$(grep -A8 'PRICE RANGE' "$OUT" | tail -8)")
  "$PY" - "$TOK" "$CHAT" "$MSG" <<'PY' >> "$OUT" 2>&1 || true
import sys, urllib.request, urllib.parse
tok, chat, msg = sys.argv[1], sys.argv[2], sys.argv[3]
data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=15)
PY
fi
