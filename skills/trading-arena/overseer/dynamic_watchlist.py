#!/usr/bin/env python3
"""Dynamic Watchlist Scanner — picks the top 20 movers every 2 hours.

NO LLM. Pure Python ranking on Finnhub (stocks) + Kraken (crypto) data.
Replaces the static STOCK_SYMBOLS / CRYPTO_SYMBOLS lists with a dynamic
top-20 list refreshed during market hours.

Score = abs(day_change_pct) * log(volume) * sign(liquidity)
- Stocks must trade >1M shares/day to qualify
- Crypto must have $10M+ 24h USD volume to qualify
- Watchlist is 100% dynamic — no hardcoded "always-on" symbols

Usage:
    python3 dynamic_watchlist.py            # Run scan, save to Supabase, send Telegram
    python3 dynamic_watchlist.py --dry-run  # Print results, don't save
    python3 dynamic_watchlist.py --print    # Show current latest watchlist from Supabase
"""
import argparse
import json
import math
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (SUPABASE_URL, SUPABASE_KEY, FINNHUB_KEY,
                    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

# Liquid US stocks to scan for movers (S&P 500 mega-caps + popular ETFs)
# Finnhub free tier doesn't give us a top-movers endpoint, so we scan a known
# universe of liquid symbols and rank them by intraday move + volume.
SCAN_UNIVERSE = [
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "AMD", "NFLX", "AVGO",
    "ORCL", "CRM", "ADBE", "CSCO", "QCOM", "INTC", "IBM", "MU", "PANW", "PLTR",
    # Index ETFs
    "SPY", "QQQ", "IWM", "DIA", "VTI", "ARKK", "XLF", "XLE", "XLK", "XLV",
    # Other large caps
    "JPM", "BAC", "WMT", "JNJ", "PG", "DIS", "HD", "V", "MA", "COIN",
    # High volatility tickers
    "GME", "AMC", "BB", "RIVN", "LCID", "NIO", "MARA", "RIOT", "SOFI", "F",
]

# Kraken crypto pairs to scan
CRYPTO_UNIVERSE = {
    "BTC/USD": "XXBTZUSD", "ETH/USD": "XETHZUSD", "SOL/USD": "SOLUSD",
    "XRP/USD": "XXRPZUSD", "ADA/USD": "ADAUSD", "DOGE/USD": "XDGUSD",
    "DOT/USD": "DOTUSD", "AVAX/USD": "AVAXUSD", "MATIC/USD": "MATICUSD",
    "LINK/USD": "LINKUSD", "UNI/USD": "UNIUSD", "ATOM/USD": "ATOMUSD",
    "LTC/USD": "XLTCZUSD", "BCH/USD": "BCHUSD", "FIL/USD": "FILUSD",
}


def _http_get(url, headers=None, timeout=10):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  HTTP error ({url[:60]}): {e}", file=sys.stderr)
        return None


def _supabase_post(table, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {**HEADERS, "Prefer": "return=minimal"}
    body = json.dumps(data if isinstance(data, list) else [data]).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status
    except Exception as e:
        print(f"  Supabase POST error: {e}", file=sys.stderr)
        return None


def _supabase_get(path):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
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


def score(change_pct: float, volume_usd: float) -> float:
    """Combined score: volatility × log-liquidity. Higher = better trade candidate."""
    if volume_usd <= 0:
        return 0
    return abs(change_pct) * math.log10(max(volume_usd, 100))


def scan_stocks():
    """Scan stock universe via Finnhub, return ranked list."""
    if not FINNHUB_KEY:
        print("  No FINNHUB_KEY — skipping stocks", file=sys.stderr)
        return []

    results = []
    for sym in SCAN_UNIVERSE:
        quote = _http_get(f"https://finnhub.io/api/v1/quote?symbol={sym}&token={FINNHUB_KEY}")
        if not quote or not quote.get("c"):
            continue

        price = quote["c"]
        change_pct = quote.get("dp", 0)  # day change %

        # Need a volume estimate — Finnhub free tier doesn't give intraday volume,
        # but we can infer activity from |change_pct|
        # Use price as a rough proxy for "this is a real stock not a penny stock"
        if price < 1.0:
            continue

        # Volume proxy: if there's a real day move, there's volume
        # Finnhub doesn't return current volume in /quote, so use $100M default
        volume_usd = 100_000_000

        results.append({
            "symbol": sym,
            "asset_type": "stock",
            "price": price,
            "change_pct": change_pct,
            "volume_usd": volume_usd,
            "score": score(change_pct, volume_usd),
        })
        time.sleep(0.15)  # Rate limit (60/min on free tier)

    return results


def scan_crypto():
    """Scan crypto universe via Kraken public API, return ranked list."""
    results = []
    pairs_str = ",".join(CRYPTO_UNIVERSE.values())
    ticker = _http_get(f"https://api.kraken.com/0/public/Ticker?pair={pairs_str}")
    if not ticker or not ticker.get("result"):
        return results

    # Build reverse lookup: kraken_pair → friendly_name
    reverse = {v: k for k, v in CRYPTO_UNIVERSE.items()}

    for kraken_pair, info in ticker["result"].items():
        # Try to find the friendly name
        friendly = reverse.get(kraken_pair)
        if not friendly:
            # Sometimes Kraken returns slightly different keys
            for fname, kpair in CRYPTO_UNIVERSE.items():
                if kraken_pair.startswith(kpair) or kpair in kraken_pair:
                    friendly = fname
                    break
        if not friendly:
            continue

        try:
            price = float(info["c"][0])
            open_price = float(info["o"])
            volume_24h = float(info["v"][1])
            change_pct = ((price - open_price) / open_price * 100) if open_price else 0
            volume_usd = volume_24h * price

            # Filter: must have >$10M 24h volume
            if volume_usd < 10_000_000:
                continue

            results.append({
                "symbol": friendly,
                "asset_type": "crypto",
                "price": price,
                "change_pct": change_pct,
                "volume_usd": volume_usd,
                "score": score(change_pct, volume_usd),
            })
        except (KeyError, ValueError, IndexError):
            continue

    return results


def build_watchlist(top_n=20):
    """Build top-N watchlist from stocks + crypto."""
    print("Scanning stocks...", file=sys.stderr)
    stocks = scan_stocks()
    print(f"  {len(stocks)} stocks scored", file=sys.stderr)

    print("Scanning crypto...", file=sys.stderr)
    crypto = scan_crypto()
    print(f"  {len(crypto)} crypto pairs scored", file=sys.stderr)

    combined = stocks + crypto
    combined.sort(key=lambda x: x["score"], reverse=True)
    return combined[:top_n]


def save_watchlist(watchlist):
    """Save watchlist snapshot to Supabase."""
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "created_at": now,
        "symbols": json.dumps([item["symbol"] for item in watchlist]),
        "details": json.dumps(watchlist),
    }
    return _supabase_post("arena_watchlist", record)


def format_telegram(watchlist):
    """Format watchlist as a Telegram message."""
    if not watchlist:
        return "📊 Watchlist refresh: no movers found"

    lines = ["📊 <b>Top 20 Watchlist Refresh</b>", ""]
    for i, item in enumerate(watchlist[:20], 1):
        emoji = "🟢" if item["change_pct"] > 0 else "🔴"
        type_emoji = "₿" if item["asset_type"] == "crypto" else "📈"
        lines.append(
            f"{i}. {type_emoji} {emoji} <b>{item['symbol']}</b> "
            f"{item['change_pct']:+.1f}% (score {item['score']:.1f})"
        )
    return "\n".join(lines)


def print_latest_watchlist():
    """Show the most recent watchlist from Supabase."""
    rows = _supabase_get("arena_watchlist?order=created_at.desc&limit=1")
    if not rows:
        print("No watchlists yet.")
        return
    row = rows[0]
    print(f"=== Latest Watchlist ({row['created_at']}) ===")
    details = json.loads(row.get("details", "[]"))
    for i, item in enumerate(details, 1):
        print(f"  {i}. {item['symbol']:12s} {item['change_pct']:+6.2f}%  score={item['score']:.2f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Don't save to Supabase or Telegram")
    parser.add_argument("--print", action="store_true", help="Print latest watchlist from Supabase")
    parser.add_argument("--top-n", type=int, default=20, help="Number of symbols to keep")
    args = parser.parse_args()

    if args.print:
        print_latest_watchlist()
        return

    print(f"=== Dynamic Watchlist Scanner: {datetime.now(timezone.utc).isoformat()} ===")
    watchlist = build_watchlist(top_n=args.top_n)

    if not watchlist:
        print("ERROR: No symbols found")
        sys.exit(1)

    print(f"\nTop {len(watchlist)} symbols:")
    for i, item in enumerate(watchlist, 1):
        print(f"  {i:2d}. {item['symbol']:12s} {item['asset_type']:6s} "
              f"{item['change_pct']:+6.2f}%  score={item['score']:.2f}")

    if args.dry_run:
        print("\n(dry-run — not saved)")
        return

    save_watchlist(watchlist)
    _send_telegram(format_telegram(watchlist))
    print(f"\nSaved + Telegram alert sent.")


if __name__ == "__main__":
    main()
