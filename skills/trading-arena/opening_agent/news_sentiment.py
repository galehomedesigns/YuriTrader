"""Lightweight, bounded news sentiment for opening-power prioritization.

NOT a governing factor — a small nudge. The ranker's technical composite (TIGHT /
location / power bar / KPIs) decides the setup; this only breaks ties and slightly
re-orders among comparable candidates so a name with a genuine fresh catalyst gets
a touch more priority (and one with a clearly bearish flag a touch less).

Two deliberate design choices keep it honest:
  1. MACRO/SECTOR headlines are ignored. Finnhub "company news" frequently tags
     index-wide stories ("Stocks Soar on US-Iran Peace", "Chip stocks jump...") to
     a ticker. Those are tape, not a company catalyst, so they earn ZERO sentiment
     — a stock whose only headlines are macro scores neutral (no free bonus).
  2. Sentiment is keyword-based and bounded to [-1, +1]; the caller multiplies by a
     small point budget (OPENING_NEWS_FACTOR, default 5) so it can never dominate.

Fail-safe: any fetch/parse error for a symbol returns neutral (0.0).
"""
import concurrent.futures
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared import news_feed  # noqa: E402

# Company-specific catalysts (positive).
_POS = (
    "upgrade", "raises price target", "raised price target", "increases price target",
    "price target raised", "lifts price target", "initiates buy", "initiated buy",
    "reiterates buy", "overweight", "outperform", "beats", "tops estimate",
    "tops estimates", "surges", "soars", "jumps", "spikes", "wins", "awarded",
    "secures", "contract", "partnership", "approval", "approved", "record high",
    "record revenue", "buyback", "guidance raised", "raises guidance",
    "strong demand", "bullish", "acquisition", "to acquire", "takeover", "upsized",
)
# Company-specific risks (negative).
_NEG = (
    "downgrade", "downgraded", "cuts price target", "lowers price target",
    "price target cut", "lowered to", "misses", "falls short", "plunges", "tumbles",
    "slumps", "sinks", "probe", "investigation", "lawsuit", "sued", "class action",
    "dilution", "secondary offering", "stock offering", "halted", "warning", "warns",
    "bearish", "recall", "short seller", "short-seller", "fraud", "liabilities",
    "going concern", "underweight", "sell rating", "guidance cut", "cuts guidance",
    "bankruptcy", "delist",
)
# Macro / sector / index stories — NOT a company catalyst, ignore for sentiment.
_MACRO = (
    "stocks ", "stock market", "s&p", "s&p500", "s&p 500", "dow ", "nasdaq",
    "wall street", "today's market", "todays market", "pre-market session",
    "premarket session", "these stocks", "sector ", "chip stocks", "tech stocks",
    "market session", "futures ", "indexes", "indices", "explain today",
    "stocks that", "movers", "on the move",
)


def _is_macro(headline, symbol):
    """Macro/sector headline that does NOT name the ticker → ignore."""
    h = headline.lower()
    if symbol.lower() in (" " + h):            # ticker explicitly named → company-specific
        return False
    return any(p in h for p in _MACRO)


def headline_sentiment(symbol, items):
    """Return ({-1..+1} sentiment, detail dict) from a symbol's headlines.
    Ignores macro headlines; nets positive vs negative company-specific keywords."""
    pos = neg = used = 0
    drivers = []
    for it in (items or []):
        h = (it.get("headline") or "")
        if not h or _is_macro(h, symbol):
            continue
        used += 1
        hl = h.lower()
        p = any(k in hl for k in _POS)
        n = any(k in hl for k in _NEG)
        if p and not n:
            pos += 1
            drivers.append("+ " + h[:90])
        elif n and not p:
            neg += 1
            drivers.append("- " + h[:90])
    raw = pos - neg
    sentiment = max(-1.0, min(1.0, raw / 2.0))   # 2+ net headlines = full magnitude
    return sentiment, {"pos": pos, "neg": neg, "used": used, "drivers": drivers[:3]}


def _one(symbol):
    try:
        return symbol, headline_sentiment(symbol, news_feed.fetch_news(symbol))
    except Exception:
        return symbol, (0.0, {"pos": 0, "neg": 0, "used": 0, "drivers": [], "error": True})


def batch(symbols, max_workers=8):
    """{symbol -> {'sentiment', 'pos', 'neg', 'used', 'drivers'}} for all symbols.
    Concurrent + fail-safe (a failing symbol comes back neutral)."""
    out = {}
    syms = list(dict.fromkeys(symbols))          # de-dupe, preserve order
    if not syms:
        return out
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for sym, (sent, detail) in ex.map(_one, syms):
            out[sym] = {"sentiment": sent, **detail}
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(batch(sys.argv[1:] or ["SNDK", "HL", "CRWV", "QCOM"]), indent=2))
