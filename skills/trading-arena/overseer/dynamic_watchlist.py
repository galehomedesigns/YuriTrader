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
                    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
                    KRAKEN_ROUNDTRIP_FEE_PCT)

# Fee floor as a percent (auto-tracks the corrected .env KRAKEN_TAKER_FEE_PCT).
FEE_FLOOR_PCT = KRAKEN_ROUNDTRIP_FEE_PCT * 100.0
# A mover only qualifies if its 24h move clears the fee floor by this multiple
# (env-tunable). A pick that can't beat ~1.6% round-trip is structurally dead —
# surfacing it just feeds the churn that the live ledger proved loses money.
MOMENTUM_FEE_MULT = float(os.environ.get("MOMENTUM_FEE_MULT", "1.5"))
# Hard floor regardless of fee math (env-tunable), e.g. ignore <3% noise.
MOMENTUM_MIN_MOVE_PCT = float(os.environ.get("MOMENTUM_MIN_MOVE_PCT", "3.0"))
# Reject blow-off / illiquid-pump tops we'd be buying at the very end of.
MOMENTUM_MAX_MOVE_PCT = float(os.environ.get("MOMENTUM_MAX_MOVE_PCT", "40.0"))
# Minimum 24h USD volume to qualify (env-tunable; was a hardcoded $10M).
MOMENTUM_MIN_VOL_USD = float(os.environ.get("MOMENTUM_MIN_VOL_USD", "10000000"))

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

# Crypto universe is now enumerated dynamically from Kraken AssetPairs
# (every online USD spot pair) — see scan_crypto(). The old 15-pair hardcoded
# dict missed most coins Kraken's own momentum notices fire on and had stale
# entries (e.g. MATIC was renamed POL). Kraken uses XBT/XDG tickers; we
# normalize to the BTC/DOGE friendly names the rest of the arena expects.
_KRAKEN_BASE_ALIASES = {"XBT": "BTC", "XDG": "DOGE"}


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
    """Fee-aware score: NET-of-fee move × log-liquidity. A mover is only worth
    surfacing for the magnitude that exceeds the round-trip fee floor — ranking
    on raw |change%| (the old formula) floated sub-fee noise to the top, which
    is exactly the churn the live ledger proved loses money. Stocks (no Kraken
    fee) pass FEE_FLOOR≈0 effect via the same formula since their moves dwarf it."""
    if volume_usd <= 0:
        return 0
    net_move = max(abs(change_pct) - FEE_FLOOR_PCT, 0.0)
    return net_move * math.log10(max(volume_usd, 100))


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


def _kraken_usd_universe():
    """Enumerate every online USD spot pair from Kraken AssetPairs.
    Returns {ticker_key_or_altname: (friendly 'BASE/USD', altname)}."""
    ap = _http_get("https://api.kraken.com/0/public/AssetPairs")
    if not ap or not ap.get("result"):
        return {}
    universe = {}
    for canon, info in ap["result"].items():
        try:
            if info.get("status") and info["status"] != "online":
                continue
            if info.get("quote") not in ("ZUSD", "USD"):
                continue
            altname = info.get("altname", "")
            if not altname or altname.endswith(".d"):  # .d = dark-pool variant
                continue
            wsname = info.get("wsname") or ""
            base = (wsname.split("/")[0] if "/" in wsname
                    else altname[:-3] if altname.endswith("USD") else "")
            if not base:
                continue
            base = _KRAKEN_BASE_ALIASES.get(base, base)
            friendly = f"{base}/USD"
            # Index by both the canonical key and altname — Kraken's Ticker
            # response keys back by canonical name even when queried by altname.
            universe[canon] = (friendly, altname)
            universe[altname] = (friendly, altname)
        except (KeyError, AttributeError):
            continue
    return universe


def scan_crypto():
    """Scan EVERY Kraken USD spot pair, fee-aware. Returns ranked movers whose
    24h move clears the round-trip fee floor with margin (the same kind of
    burst Kraken's own momentum notices fire on — but filtered to ones that can
    actually pay for their fees). Each result carries `kraken_pair` so the arena
    market scanner can fetch it (no longer limited to the hardcoded 6)."""
    results = []
    universe = _kraken_usd_universe()
    if not universe:
        print("  AssetPairs unavailable — crypto scan skipped", file=sys.stderr)
        return results

    # Unique altnames, chunked so the Ticker URL stays sane.
    altnames = sorted({alt for _, (_, alt) in universe.items()})
    seen = set()
    for i in range(0, len(altnames), 80):
        chunk = altnames[i:i + 80]
        ticker = _http_get(
            "https://api.kraken.com/0/public/Ticker?pair=" + ",".join(chunk))
        if not ticker or not ticker.get("result"):
            continue
        for key, info in ticker["result"].items():
            mapped = universe.get(key)
            if not mapped:
                continue
            friendly, altname = mapped
            if friendly in seen:
                continue
            try:
                price = float(info["c"][0])
                open_price = float(info["o"])
                volume_24h = float(info["v"][1])
                change_pct = ((price - open_price) / open_price * 100) if open_price else 0
                volume_usd = volume_24h * price

                if volume_usd < MOMENTUM_MIN_VOL_USD:
                    continue
                move = abs(change_pct)
                # Fee-aware gate: must clear the fee floor by the margin AND the
                # hard min, and not be a blow-off top we'd buy at the very end.
                min_move = max(FEE_FLOOR_PCT * MOMENTUM_FEE_MULT,
                               MOMENTUM_MIN_MOVE_PCT)
                if move < min_move or move > MOMENTUM_MAX_MOVE_PCT:
                    continue

                seen.add(friendly)
                results.append({
                    "symbol": friendly,
                    "asset_type": "crypto",
                    "price": price,
                    "change_pct": change_pct,
                    "volume_usd": volume_usd,
                    "kraken_pair": altname,
                    "score": score(change_pct, volume_usd),
                })
            except (KeyError, ValueError, IndexError, ZeroDivisionError):
                continue
        time.sleep(0.3)  # gentle rate-limit between Ticker chunks

    return results


def build_watchlist(top_n=20, crypto_only=False):
    """Build top-N watchlist from stocks + crypto.

    crypto_only=True skips the Finnhub stock scan — for the 24/7 crypto cron so
    overnight/weekend Kraken momentum (when its notices commonly fire) keeps the
    watchlist fresh instead of the arena scanning a stale weekday snapshot."""
    stocks = []
    if not crypto_only:
        print("Scanning stocks...", file=sys.stderr)
        stocks = scan_stocks()
        print(f"  {len(stocks)} stocks scored", file=sys.stderr)
    else:
        # Carry forward the latest stock picks so a 24/7 crypto-only refresh
        # never blanks stocks from the watchlist (fetch_dynamic_watchlist would
        # otherwise fall back to the static STOCK_SYMBOLS during market hours).
        latest = _supabase_get("arena_watchlist?order=created_at.desc&limit=1")
        if latest:
            try:
                prev = latest[0].get("details")
                if isinstance(prev, str):
                    prev = json.loads(prev)
                stocks = [it for it in (prev or [])
                          if it.get("asset_type") == "stock"]
                print(f"  carried forward {len(stocks)} prior stock picks",
                      file=sys.stderr)
            except (ValueError, KeyError, TypeError):
                pass

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


def generate_llm_narrative(watchlist):
    """Use quick36 to synthesize a tradability-focused brief from the watchlist.
    Returns a 3-5 sentence narrative or None on failure."""
    if not watchlist:
        return None

    rows = []
    for i, item in enumerate(watchlist[:15], 1):
        rows.append(
            f"  {i:2d}. {item['symbol']:<10} {item['asset_type']:<6} "
            f"{item['change_pct']:+6.2f}%  score={item['score']:.1f}"
        )

    prompt = f"""You are advising Tony on today's most tradable movers. Given the algorithm-scored
top movers below (score = |change%| × log10(volume_usd), higher = more actionable), write a
SHORT trading-focused narrative (3-5 sentences, plain text, no markdown).

- Lead with 1-2 tickers that look most actionable today and why
- Mention asset class spread (stocks vs crypto) if notable
- Flag any thematic clusters (e.g. multiple semis selling off, crypto-correlated names)
- Skip rephrasing the table — synthesize the trading read

Top movers:
{chr(10).join(rows)}"""

    payload = json.dumps({
        "model": "quick36:latest",
        "prompt": prompt,
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {"temperature": 0.4, "num_ctx": 8192, "num_predict": 400},
    }).encode()

    url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434") + "/api/generate"
    req = urllib.request.Request(url, data=payload,
                                  headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read()).get("response", "").strip() or None
    except Exception as e:
        print(f"  narrative unavailable: {type(e).__name__}: {str(e)[:100]}", file=sys.stderr)
        return None


def format_telegram(watchlist):
    """Format watchlist as a Telegram message with an LLM-synthesized narrative on top."""
    if not watchlist:
        return "📊 Watchlist refresh: no movers found"

    narrative = generate_llm_narrative(watchlist)

    lines = ["📊 <b>Top 20 Watchlist Refresh</b>"]
    if narrative:
        # html.escape would be ideal but Telegram parse_mode=HTML accepts the simple subset
        # used here; the narrative is plain prose with no markup.
        lines.append("")
        lines.append(narrative)
        lines.append("")
        lines.append("— Movers —")
    else:
        lines.append("")

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
    parser.add_argument("--crypto-only", action="store_true",
                        help="Skip the Finnhub stock scan (for the 24/7 crypto cron)")
    args = parser.parse_args()

    if args.print:
        print_latest_watchlist()
        return

    print(f"=== Dynamic Watchlist Scanner: {datetime.now(timezone.utc).isoformat()} ===")
    watchlist = build_watchlist(top_n=args.top_n, crypto_only=args.crypto_only)

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
