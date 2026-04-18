#!/usr/bin/env python3
"""Stock Position Watcher — runs every 5 min via cron during US market hours.

For each open manual STOCK position (symbols without "/" in the name), fetches
current market data from Finnhub+TwelveData, runs should_exit() on every bot,
and if ANY bot fires an exit signal sends a Telegram alert via YuriTradingViewBot
with Sell/Hold buttons.

The stock_concierge daemon handles the button taps.

Usage:
    python3 stock_position_watcher.py          # Check all manual stock positions
    python3 stock_position_watcher.py --force  # Send alert even if no signals
"""
import argparse
import html
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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

from shared.market_scanner import fetch_stock_data
from concierge import state

# Import all 10 bots for exit signal consensus
from bots.momentum_hunter import MomentumHunter
from bots.the_reverter import TheReverter
from bots.nano_sniper import NanoSniper
from bots.trend_rider import TrendRider
from bots.squeeze_breaker import SqueezeBreaker
from bots.flag_rider import FlagRider
from bots.trap_catcher import TrapCatcher
from bots.volume_whisperer import VolumeWhisperer
from bots.correlation_hunter import CorrelationHunter
from bots.news_sniper import NewsSniper


SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
STOCK_BOT_TOKEN = os.environ.get("TELEGRAM_STOCK_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6545739863")


# ========== Market hours ==========

def in_market_hours():
    """True during NYSE/NASDAQ regular hours (9:30–16:00 ET, Mon-Fri)."""
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:
        return False
    hhmm = now_et.hour * 100 + now_et.minute
    return 930 <= hhmm < 1600


# ========== Supabase ==========

def _sb_get(path):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return []


# ========== Telegram ==========

def send_tg_with_buttons(text, buttons):
    if not STOCK_BOT_TOKEN:
        print("  STOCK_BOT_TOKEN not set — skipping Telegram", file=sys.stderr)
        return
    url = f"https://api.telegram.org/bot{STOCK_BOT_TOKEN}/sendMessage"
    params = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": json.dumps({"inline_keyboard": buttons}),
    }
    try:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(params).encode(), method="POST")
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"  TG error: {e}", file=sys.stderr)


# ========== Bot setup ==========

def _get_all_bots():
    return [
        MomentumHunter(), TheReverter(), NanoSniper(), TrendRider(),
        SqueezeBreaker(), FlagRider(), TrapCatcher(), VolumeWhisperer(),
        CorrelationHunter(), NewsSniper(),
    ]


def check_exit_consensus(position, data, bots):
    """Run should_exit() for every bot on the given position+data."""
    signals = []
    for bot in bots:
        try:
            reason = bot.should_exit(position, data)
            if reason:
                signals.append((bot.NAME, reason))
        except Exception:
            continue
    return signals


# ========== Main ==========

def check_positions(force=False):
    if not force and not in_market_hours():
        print("Outside US market hours — skipping stock position check.")
        return

    positions = _sb_get(
        "arena_trades?status=eq.open&manual_trade=eq.true"
        "&select=id,bot_id,bot_name,symbol,entry_price,qty,opened_at,reason"
    )
    if not positions:
        print("No open manual positions to check.", file=sys.stderr)
        return

    # Filter to STOCK positions only — symbol convention: "/" means crypto pair
    stock_positions = [p for p in positions if "/" not in (p.get("symbol") or "")]
    if not stock_positions:
        print("No stock positions to monitor.", file=sys.stderr)
        return

    print(f"Checking {len(stock_positions)} stock position(s)", file=sys.stderr)

    # Fetch live data for unique stock symbols
    stock_symbols = list(set(p["symbol"] for p in stock_positions))
    market_data = fetch_stock_data(stock_symbols)
    bots = _get_all_bots()

    for pos in stock_positions:
        symbol = pos["symbol"]
        trade_id = pos["id"]
        data = market_data.get(symbol)

        if not data:
            print(f"  [{symbol}] No market data available", file=sys.stderr)
            continue

        if state.is_muted(trade_id) and not force:
            print(f"  [{symbol} id={trade_id}] Muted, skipping", file=sys.stderr)
            continue

        signals = check_exit_consensus(pos, data, bots)

        if not signals and not force:
            print(f"  [{symbol} id={trade_id}] No exit signals", file=sys.stderr)
            continue

        entry = float(pos.get("entry_price") or 0)
        qty = float(pos.get("qty") or 0)
        current = data.price
        pnl = (current - entry) * qty
        pnl_pct = (current - entry) / entry * 100 if entry else 0

        emoji = "⚠" if pnl >= 0 else "🚨"
        reasons_text = "\n".join(
            f"• <b>{html.escape(name)}</b>: {html.escape(reason)}"
            for name, reason in signals[:5]
        )
        if not reasons_text:
            reasons_text = "• (forced check — no actual signals)"

        currency = "CAD" if symbol.upper().endswith(".TO") else "USD"

        text = (
            f"{emoji} <b>{html.escape(symbol)} SELL SIGNAL</b> (id {trade_id})\n"
            f"Entry: ${entry:,.2f} → Now: ${current:,.2f} {currency}\n"
            f"P&amp;L: ${pnl:+.2f} ({pnl_pct:+.1f}%)  ({qty:.0f} shares)\n\n"
            f"<b>{len(signals)} bot(s) agree to exit:</b>\n"
            f"{reasons_text}"
        )

        sell_cb = state.save_pending_action(
            "sell", symbol=symbol, trade_id=trade_id,
            context={
                "symbol": symbol, "entry": entry, "current": current,
                "pnl": pnl, "qty": qty, "source": "stock_position_watcher",
            },
        )
        hold_cb = state.save_pending_action(
            "hold", symbol=symbol, trade_id=trade_id
        )
        buttons = [[
            {"text": "✅ Sell Now", "callback_data": sell_cb},
            {"text": "🔇 Hold (30m)", "callback_data": hold_cb},
        ]]

        send_tg_with_buttons(text, buttons)
        print(f"  [{symbol} id={trade_id}] Alert sent — {len(signals)} signals", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Send alert even outside market hours / with no signals")
    args = parser.parse_args()
    check_positions(force=args.force)


if __name__ == "__main__":
    main()
