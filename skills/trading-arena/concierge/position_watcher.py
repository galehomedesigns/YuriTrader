#!/usr/bin/env python3
"""Position Watcher — runs every 5 min via cron during market hours.

For each open manual position, fetches current market data, runs should_exit()
on every bot, and if ANY bot fires an exit signal sends a Telegram alert with
Sell/Hold/Tighten buttons.

The concierge (trading_concierge.py) handles the button taps.

Usage:
    python3 position_watcher.py       # Check all manual positions, alert if needed
    python3 position_watcher.py --force  # Send alert even if no signals (for testing)
"""
import argparse
import html
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_env():
    """Load .env into os.environ (cron doesn't inherit the shell env)."""
    env_file = "/home/tonygale/openclaw/.env"
    if not os.path.exists(env_file):
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key and value:
                os.environ.setdefault(key, value)


_load_env()

from shared.market_scanner import fetch_crypto_data
from shared.kraken_executor import KRAKEN_PAIR_MAP
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
TRADER_BOT_TOKEN = os.environ.get("TELEGRAM_TRADER_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6545739863")


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


def send_tg_with_buttons(text, buttons):
    """Send a Telegram message via the trader bot with inline buttons."""
    if not TRADER_BOT_TOKEN:
        print("  TRADER_BOT_TOKEN not set — skipping Telegram", file=sys.stderr)
        return
    url = f"https://api.telegram.org/bot{TRADER_BOT_TOKEN}/sendMessage"
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


def _get_all_bots():
    return [
        MomentumHunter(), TheReverter(), NanoSniper(), TrendRider(),
        SqueezeBreaker(), FlagRider(), TrapCatcher(), VolumeWhisperer(),
        CorrelationHunter(), NewsSniper(),
    ]


def check_exit_consensus(position, data, bots):
    """Run should_exit() for every bot on the given position+data.

    Returns a list of (bot_name, exit_reason) for any bot that would exit.
    """
    signals = []
    for bot in bots:
        try:
            reason = bot.should_exit(position, data)
            if reason:
                signals.append((bot.NAME, reason))
        except Exception:
            continue
    return signals


def check_positions(force=False):
    """Main entry point — check all open manual positions."""
    positions = _sb_get(
        "arena_trades?status=eq.open&manual_trade=eq.true"
        "&select=id,bot_id,bot_name,symbol,entry_price,qty,opened_at,reason"
    )
    if not positions:
        print("No open manual positions to check.", file=sys.stderr)
        return

    print(f"Checking {len(positions)} manual position(s)", file=sys.stderr)

    # Fetch current crypto market data
    crypto_symbols = list(set(p["symbol"] for p in positions if p["symbol"] in KRAKEN_PAIR_MAP))
    if not crypto_symbols:
        print("No crypto positions to monitor (only crypto supported).", file=sys.stderr)
        return

    market_data = fetch_crypto_data(crypto_symbols)
    bots = _get_all_bots()

    for pos in positions:
        symbol = pos["symbol"]
        trade_id = pos["id"]
        data = market_data.get(symbol)

        if not data:
            print(f"  [{symbol}] No market data available", file=sys.stderr)
            continue

        # Check if this position is muted
        if state.is_muted(trade_id) and not force:
            print(f"  [{symbol} id={trade_id}] Muted, skipping", file=sys.stderr)
            continue

        signals = check_exit_consensus(pos, data, bots)

        if not signals and not force:
            print(f"  [{symbol} id={trade_id}] No exit signals", file=sys.stderr)
            continue

        # Compute P&L
        entry = float(pos.get("entry_price") or 0)
        qty = float(pos.get("qty") or 0)
        current = data.price
        pnl = (current - entry) * qty
        pnl_pct = (current - entry) / entry * 100 if entry else 0

        emoji = "⚠" if pnl >= 0 else "🚨"
        # Escape HTML in bot reasons — they contain raw < > characters
        # (e.g. "EMA21 < EMA50") that break Telegram's HTML parser.
        reasons_text = "\n".join(
            f"• <b>{html.escape(name)}</b>: {html.escape(reason)}"
            for name, reason in signals[:5]
        )
        if not reasons_text:
            reasons_text = "• (forced check — no actual signals)"

        text = (
            f"{emoji} <b>{html.escape(symbol)} SELL SIGNAL</b> (id {trade_id})\n"
            f"Entry: ${entry:,.4f} → Now: ${current:,.4f}\n"
            f"P&amp;L: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n\n"
            f"<b>{len(signals)} bot(s) agree to exit:</b>\n"
            f"{reasons_text}"
        )

        # Build button callbacks
        sell_cb = state.save_pending_action(
            "sell", symbol=symbol, trade_id=trade_id,
            context={"symbol": symbol, "entry": entry, "current": current, "pnl": pnl}
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
    parser.add_argument("--force", action="store_true", help="Send alert even with no signals")
    args = parser.parse_args()

    check_positions(force=args.force)


if __name__ == "__main__":
    main()
