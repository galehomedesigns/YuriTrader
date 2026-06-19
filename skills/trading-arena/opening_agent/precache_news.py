#!/usr/bin/env python3
"""Pre-cache news sentiment ~1.5h before the open (run ~8:00 ET).

The time-critical 9:20 scan must deep-evaluate up to OPENING_SCAN_LIMIT (300)
movers inside a ~10-min window, so it can't also run an LLM news pass on every
candidate. Breaking COMPANY news in the final 90 min is rare, and sentiment is
only a bounded ±OPENING_NEWS_FACTOR nudge — so we compute it once, early, over a
broad mover universe and cache it. run_opening_scan then just reads the cache.

Writes logs/news_sentiment_cache.json:
  {"et_date": "YYYY-MM-DD", "ts_utc": "...", "symbols": N,
   "sentiment": {SYMBOL: {sentiment, pos, neg, used, drivers}}}

  precache_news.py            # fetch + cache
  precache_news.py --print    # also print a summary
"""
import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_env():
    p = "/home/tonygale/openclaw/.env"
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if k and v:
                    os.environ.setdefault(k, v)


_load_env()
from opening_agent import tv_screener, news_sentiment      # noqa: E402

CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "logs", "news_sentiment_cache.json")


def main():
    limit = int(os.environ.get("OPENING_SCAN_LIMIT", "300"))
    min_price = float(os.environ.get("OPENING_MIN_PRICE", "5"))
    min_pmvol = int(os.environ.get("OPENING_MIN_PREMARKET_VOLUME", "50000"))
    min_gap = float(os.environ.get("OPENING_SCAN_MIN_GAP_PCT", "1"))
    max_gap = float(os.environ.get("OPENING_SCAN_MAX_GAP_PCT", "25"))
    both = os.environ.get("OPENING_ALLOW_SHORTS", "false").lower() == "true"

    raw = tv_screener.movers(limit=limit, min_price=min_price,
                             min_premarket_vol=min_pmvol, min_gap=min_gap, max_gap=max_gap)
    if both:
        raw += tv_screener.movers(limit=limit, min_price=min_price,
                                  min_premarket_vol=min_pmvol, min_gap=min_gap,
                                  max_gap=max_gap, losers=True)
    symbols = list(dict.fromkeys(m["symbol"] for m in raw))[:limit]
    print(f"[news-precache] {len(symbols)} mover symbols to sentiment-score...", file=sys.stderr)

    sentiment = news_sentiment.batch(symbols) if symbols else {}
    et = datetime.now(ZoneInfo("America/New_York"))
    out = {
        "et_date": et.strftime("%Y-%m-%d"),
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "symbols": len(symbols),
        "sentiment": sentiment,
    }
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f)
    os.replace(tmp, CACHE)
    nonzero = sum(1 for v in sentiment.values() if v.get("sentiment"))
    print(f"[news-precache] cached {len(sentiment)} symbols ({nonzero} non-neutral) -> {CACHE}",
          file=sys.stderr)
    if "--print" in sys.argv:
        movers = sorted(sentiment.items(), key=lambda kv: -abs(kv[1].get("sentiment", 0)))[:10]
        for s, d in movers:
            print(f"  {s}: {d.get('sentiment'):+.2f} (pos {d.get('pos')}, neg {d.get('neg')})")


if __name__ == "__main__":
    main()
