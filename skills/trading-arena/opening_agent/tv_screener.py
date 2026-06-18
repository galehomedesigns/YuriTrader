#!/usr/bin/env python3
"""Pre-market mover scan via TradingView's public scanner API — the production
whole-market mover source (TOP_PERC_GAIN equivalent).

scanner.tradingview.com/america/scan is a public POST endpoint (no auth, no
browser, no CDP). We ask for US common stocks above a price/volume floor, sorted
by pre-market % change, and return the top N as movers. Restricts to individual
common stocks (type=stock excludes ETFs/leveraged funds), price>=5, real
pre-market participation.

    tv_screener.py [--limit 50] [--min-price 5] [--min-pmvol 50000] [--losers]
"""
import json
import os
import sys
import urllib.request

SCAN_URL = "https://scanner.tradingview.com/america/scan"


def movers(limit=50, min_price=5.0, min_premarket_vol=50000,
           min_gap=1.0, max_gap=6.0,
           exchanges=("AMEX", "NASDAQ", "NYSE"), losers=False, common_only=True):
    """Pre-market movers re-aimed for the opening-range edge: COILED names just
    STARTING to move, not the most-extended gappers (which are always WIDE and
    never pass the TIGHT gate). Filters a MODERATE directional band
    (min_gap..max_gap %) and sorts by RELATIVE VOLUME (rising participation), so
    the funnel surfaces names that can actually be TIGHT-and-breaking.

    Returns dicts: {symbol, exchange, close, premarket_change, premarket_volume,
    change, volume, rel_volume, volatility, direction}. direction +1 / -1."""
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
    # Moderate directional band — exclude the already-extended runaways (always
    # WIDE) and the dead names. Gainers use +band, losers the mirror -band.
    if losers:
        flt.append({"left": "premarket_change", "operation": "less", "right": -float(min_gap)})
        flt.append({"left": "premarket_change", "operation": "greater", "right": -float(max_gap)})
    else:
        flt.append({"left": "premarket_change", "operation": "greater", "right": float(min_gap)})
        flt.append({"left": "premarket_change", "operation": "less", "right": float(max_gap)})
    body = {
        "filter": flt,
        "options": {"lang": "en"},
        "markets": ["america"],
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": ["name", "close", "premarket_change", "premarket_volume",
                    "change", "volume", "relative_volume_10d_calc", "Volatility.D"],
        "sort": {"sortBy": "relative_volume_10d_calc", "sortOrder": "desc"},
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
            "change": d[4], "volume": d[5], "rel_volume": d[6], "volatility": d[7],
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
        min_gap=float(opt("--min-gap", "1")),
        max_gap=float(opt("--max-gap", "6")),
        losers="--losers" in a,
    )
    if "--json" in a:
        print(json.dumps(rows)); return
    print(f"{len(rows)} movers (pre-market {'losers' if '--losers' in a else 'gainers'}, "
          f"moderate band, by rel-volume):")
    for m in rows:
        print(f"  {m['exchange']:>6}:{m['symbol']:<7} ${m['close']:>9.2f}  "
              f"pm {m['premarket_change']:+6.2f}%  relVol {m['rel_volume'] or 0:>5.1f}x  "
              f"vol% {m['volatility'] or 0:>6.1f}  pmVol {m['premarket_volume']:>11,}")


if __name__ == "__main__":
    main()
