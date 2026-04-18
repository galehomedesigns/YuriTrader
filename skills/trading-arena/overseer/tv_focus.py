#!/usr/bin/env python3
"""TradingView Focus Switcher — auto-switches headless Chromium chart to the
top opportunity from the dynamic watchlist.

Talks directly to Chromium CDP (port 9222) — no MCP, no LLM.
Reads latest watchlist from Supabase, picks #1, switches the chart symbol
via TradingView's internal API, and sends a Telegram notification.

Usage:
    python3 tv_focus.py            # Switch to top opportunity, notify
    python3 tv_focus.py --dry-run  # Show what would happen, don't switch
    python3 tv_focus.py --symbol AAPL  # Force-switch to a specific symbol
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SUPABASE_URL, SUPABASE_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

CDP_HOST = "127.0.0.1"
CDP_PORT = 9222


def _http_get(url, timeout=10):
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  HTTP error: {e}", file=sys.stderr)
        return None


def _supabase_get(path):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return []


def _send_telegram(message):
    if not TELEGRAM_TOKEN:
        return
    try:
        data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def get_top_symbol():
    """Get the highest-scoring symbol from the most recent watchlist."""
    rows = _supabase_get("arena_watchlist?order=created_at.desc&limit=1")
    if not rows:
        return None
    details = rows[0].get("details")
    if isinstance(details, str):
        details = json.loads(details)
    if not details:
        return None
    return details[0]


def to_tv_symbol(symbol: str, asset_type: str) -> str:
    """Convert internal symbol to TradingView ticker format."""
    if asset_type == "crypto":
        # Convert "BTC/USD" -> "KRAKEN:BTCUSD"
        return f"KRAKEN:{symbol.replace('/', '')}"
    return symbol


def switch_chart_symbol(tv_symbol: str) -> bool:
    """Switch TradingView chart to the given symbol via CDP using a Node helper."""
    import subprocess
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tv_switch_symbol.js")
    try:
        result = subprocess.run(
            ["node", "--experimental-vm-modules", helper, tv_symbol],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            print(f"  Node CDP error: {result.stderr.strip()}", file=sys.stderr)
            return False
        out = (result.stdout or "").strip()
        print(f"  CDP response: {out}", file=sys.stderr)
        try:
            data = json.loads(out)
            return bool(data.get("ok"))
        except json.JSONDecodeError:
            return "ok" in out.lower()
    except subprocess.TimeoutExpired:
        print("  CDP call timed out", file=sys.stderr)
        return False
    except Exception as e:
        print(f"  CDP call error: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--symbol", help="Force-switch to specific symbol")
    args = parser.parse_args()

    if args.symbol:
        target = {"symbol": args.symbol, "asset_type": "stock", "change_pct": 0, "score": 0}
    else:
        target = get_top_symbol()
        if not target:
            print("No watchlist available")
            sys.exit(1)

    tv_sym = to_tv_symbol(target["symbol"], target.get("asset_type", "stock"))
    print(f"Top opportunity: {target['symbol']} -> TV symbol: {tv_sym}")
    print(f"  Change: {target.get('change_pct', 0):+.2f}%  Score: {target.get('score', 0):.1f}")

    if args.dry_run:
        print("(dry-run)")
        return

    if switch_chart_symbol(tv_sym):
        emoji = "🟢" if target.get("change_pct", 0) > 0 else "🔴"
        msg = (f"👁 <b>TradingView Focus</b>\n"
               f"{emoji} Now monitoring: <b>{target['symbol']}</b>\n"
               f"Change: {target.get('change_pct', 0):+.2f}%\n"
               f"Score: {target.get('score', 0):.1f}")
        _send_telegram(msg)
        print("Telegram sent.")


if __name__ == "__main__":
    main()
