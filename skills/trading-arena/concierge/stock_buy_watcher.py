#!/usr/bin/env python3
"""Stock Buy Watcher — proactive BUY-signal alerts via YuriTradingViewBot.

Mirrors buy_watcher.py but for equities instead of crypto. Runs via system
cron every 30 min. For each run:
  1. Checks that US market is open (9:30–16:00 ET, Mon-Fri). Exits silently otherwise.
  2. Calls advisor.get_top_opportunity(1, asset_class="stock") — same brain as /best.
  3. If firing_count >= STOCK_BUY_WATCHER_MIN_FIRING, sends a Telegram alert
     with share-count Buy buttons (1 / 5 / 10 shares).
  4. Dedups: same symbol within 2h is suppressed unless firing_count grows.

Callback buttons go through the stock_concierge daemon — it reads the shared
concierge_state.db where we save pending actions.
"""
import html
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo


def _load_env():
    env_file = "/docker/openclaw-xrt9/.env"
    if not os.path.exists(env_file):
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key and value:
                os.environ.setdefault(key, value)

_load_env()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from concierge import advisor
from concierge import state


# ========== Config ==========

STOCK_BOT_TOKEN = os.environ.get("TELEGRAM_STOCK_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6545739863")
API_BASE = f"https://api.telegram.org/bot{STOCK_BOT_TOKEN}"

ALERT_MIN_FIRING = int(os.environ.get("STOCK_BUY_WATCHER_MIN_FIRING", "3"))
DEDUP_WINDOW_MIN = 120  # Suppress repeat alerts for same symbol within 2h

# Share-count buttons (cost shown dynamically at send time)
SHARE_AMOUNTS = [1, 5, 10]

STATE_FILE = "/tmp/stock_buy_watcher_state.json"


# ========== Market hours ==========

def in_market_hours():
    """True during NYSE/NASDAQ regular hours (9:30 AM – 4:00 PM ET, Mon-Fri)."""
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:
        return False
    hhmm = now_et.hour * 100 + now_et.minute
    return 930 <= hhmm < 1600


# ========== Telegram ==========

def send_message(text, keyboard=None):
    if not STOCK_BOT_TOKEN:
        print("TELEGRAM_STOCK_BOT_TOKEN not set", file=sys.stderr)
        return False
    params = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }
    if keyboard:
        params["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"{API_BASE}/sendMessage", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
            return payload.get("ok")
    except Exception as e:
        print(f"  TG error: {e}", file=sys.stderr)
        return False


# ========== Dedup state ==========

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(data):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"  state save error: {e}", file=sys.stderr)


def should_suppress(symbol, firing_count, prev):
    if not prev or prev.get("symbol") != symbol:
        return False
    age_sec = time.time() - prev.get("ts", 0)
    if age_sec > DEDUP_WINDOW_MIN * 60:
        return False
    if firing_count > prev.get("firing", 0):
        return False
    return True


# ========== Main ==========

def main():
    if not in_market_hours():
        print("Outside US market hours — skipping.")
        return

    try:
        results = advisor.get_top_opportunity(top_n=1, asset_class="stock")
    except Exception as e:
        print(f"Advisor error: {e}", file=sys.stderr)
        return

    if not results or "error" in results[0]:
        print(f"No opportunities found. {results[0] if results else ''}")
        return

    top = results[0]
    sym = top["symbol"]
    firing = top["firing_count"]

    if firing < ALERT_MIN_FIRING:
        print(f"{sym}: only {firing}/9 bots firing (threshold {ALERT_MIN_FIRING}) — no alert.")
        return

    prev = load_state()
    if should_suppress(sym, firing, prev):
        print(f"{sym}: duplicate of recent alert ({firing}/9) — suppressed.")
        return

    # Build the message — same layout as crypto buy_watcher, stock framing
    price = top["price"]
    change = top["day_change_pct"]
    firing_names = ", ".join(top["firing_bots"]) if top["firing_bots"] else "none"
    levels = top["levels"]
    ind = top["indicators"]
    currency = "CAD" if sym.upper().endswith(".TO") else "USD"

    text = (
        f"🟢 <b>STOCK BUY SIGNAL — {html.escape(sym)}</b>  "
        f"({firing}/9 bots agree)\n"
        f"<b>Price:</b> ${price:,.2f} {currency}  ({change:+.2f}% day)\n\n"
        f"<b>📊 Indicators:</b>\n"
        f"• RSI(14): {ind['rsi_14']:.0f}\n"
        f"• ADX(14): {ind['adx_14']:.0f}\n"
        f"• BB width: {ind['bb_bandwidth']:.4f}\n"
        f"• Candle: {html.escape(ind['candlestick'] or 'none')}\n"
        f"• ATR(14): ${ind['atr_14']:.4f}\n\n"
        f"<b>🤖 Bot consensus:</b>\n{html.escape(firing_names)}\n\n"
        f"<b>📈 Suggested levels:</b>\n"
        f"• Entry:  ${levels['entry']:,.2f}\n"
        f"• Stop:   ${levels['stop']:,.2f} ({levels['stop_pct']:+.1f}%)\n"
        f"• Target: ${levels['target']:,.2f} ({levels['target_pct']:+.1f}%)\n"
        f"• R:R:    {levels['rr']:.1f}:1\n\n"
        f"<b>💬 Analysis:</b>\n<i>{html.escape(top['analysis'])}</i>\n\n"
        f"<i>Auto-scan every 30 min, 9:30 AM–4 PM ET Mon-Fri.</i>"
    )

    # Inline share-count buttons — cost shown at send time
    context = {
        "symbol": sym, "price": price, "firing": firing,
        "levels": levels, "indicators": ind,
        "currency": currency, "source": "stock_buy_watcher",
    }
    row = []
    for qty in SHARE_AMOUNTS:
        total = qty * price
        # Reuse existing amount_usd column to store qty for stock callbacks
        cb = state.save_pending_action(
            "buy", symbol=sym, amount_usd=qty, context=context
        )
        label = f"Buy {qty} sh (${total:,.0f})"
        row.append({"text": label, "callback_data": cb})
    buttons = [row]
    skip_cb = state.save_pending_action("skip", symbol=sym, context=context)
    buttons.append([{"text": "❌ Skip", "callback_data": skip_cb}])

    ok = send_message(text, keyboard=buttons)
    if ok:
        print(f"Alert sent: {sym} ({firing}/9 bots, price ${price:.2f})")
        save_state({"symbol": sym, "firing": firing, "ts": time.time()})
    else:
        print(f"Alert send FAILED: {sym} ({firing}/9 bots)", file=sys.stderr)


if __name__ == "__main__":
    main()
