#!/usr/bin/env python3
"""Pre-market mover scan via TradingView's public scanner API — the IBKR-free
replacement for universe.IBKRMovers (TOP_PERC_GAIN).

scanner.tradingview.com/america/scan is a public POST endpoint (no auth, no
browser, no CDP). We ask for US common stocks above a price/volume floor, sorted
by pre-market % change, and return the top N as movers. Mirrors the IBKR scanner's
intent: individual common stocks (type=stock excludes ETFs/leveraged funds, like
IBKR's stockTypeFilter=CORP), price>=5, real pre-market participation.

    tv_screener.py [--limit 50] [--min-price 5] [--min-pmvol 50000] [--losers]
"""
import json
import os
import sys
import urllib.request

SCAN_URL = "https://scanner.tradingview.com/america/scan"


def movers(limit=50, min_price=5.0, min_premarket_vol=50000,
           exchanges=("AMEX", "NASDAQ", "NYSE"), losers=False, common_only=True):
    """Return list of dicts: {symbol, exchange, close, premarket_change,
    premarket_volume, change, volume, direction}. direction +1 gainers / -1 losers."""
    flt = [
        {"left": "exchange", "operation": "in_range", "right": list(exchanges)},
        {"left": "close", "operation": "greater", "right": float(min_price)},
        {"left": "premarket_volume", "operation": "greater", "right": int(min_premarket_vol)},
    ]
    if common_only:
        # type=stock excludes ETFs/funds/structured (leveraged ETNs etc.) — the
        # CORP equivalent. typespecs "common" drops preferred/depositary shares.
        flt.append({"left": "type", "operation": "in_range", "right": ["stock"]})
        flt.append({"left": "typespecs", "operation": "has", "right": ["common"]})
    body = {
        "filter": flt,
        "options": {"lang": "en"},
        "markets": ["america"],
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": ["name", "close", "premarket_change", "premarket_volume",
                    "change", "volume"],
        "sort": {"sortBy": "premarket_change",
                 "sortOrder": "asc" if losers else "desc"},
        "range": [0, int(limit)],
    }
    req = urllib.request.Request(
        SCAN_URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        j = json.load(r)
    out = []
    for row in j.get("data", []):
        ex, _, tick = row["s"].partition(":")
        d = row["d"]
        out.append({
            "symbol": tick, "exchange": ex,
            "close": d[1], "premarket_change": d[2], "premarket_volume": d[3],
            "change": d[4], "volume": d[5],
            "direction": -1 if losers else 1,
        })
    return out


def main():
    a = sys.argv
    def opt(name, default):
        return a[a.index(name) + 1] if name in a else default
    rows = movers(
        limit=int(opt("--limit", "50")),
        min_price=float(opt("--min-price", "5")),
        min_premarket_vol=int(opt("--min-pmvol", "50000")),
        losers="--losers" in a,
    )
    if "--json" in a:
        print(json.dumps(rows)); return
    print(f"{len(rows)} movers (pre-market {'losers' if '--losers' in a else 'gainers'}):")
    for m in rows:
        print(f"  {m['exchange']:>6}:{m['symbol']:<7} ${m['close']:>9.2f}  "
              f"pm {m['premarket_change']:+7.2f}%  pmVol {m['premarket_volume']:>12,}")


if __name__ == "__main__":
    main()
