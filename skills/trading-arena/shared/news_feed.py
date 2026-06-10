"""Untrusted news feed for the LLM advisor (Finnhub-backed).

Returns recent headlines as the UNTRUSTED context the advisor weighs. This is an
attack surface by design (see ../LLM_ADVISOR_DESIGN.md §5): the text is fenced +
sanitised inside llm_advisor._build_prompt and can never alter caps/gates — the
subtract-only validator bounds any manipulation to a 'failure to veto'.

Fully defensive: no key, network error, bad payload, or rate-limit → returns []
(the advisor then just runs on numeric signals). Never raises to the caller.

Sources:
  - stock symbol (e.g. AAPL) -> Finnhub company-news (last 3 days)
  - crypto symbol (e.g. BTC/USD) -> Finnhub general crypto news, filtered to the
    base asset by ticker / common name
"""
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "")
MAX_HEADLINES = int(os.environ.get("ADVISOR_NEWS_MAX", "3"))
NEWS_TIMEOUT_S = float(os.environ.get("ADVISOR_NEWS_TIMEOUT_S", "8"))

# Base-ticker -> name aliases for filtering general crypto news to a symbol.
_CRYPTO_NAMES = {
    "BTC": ["bitcoin", "btc"], "ETH": ["ethereum", "ether", "eth"],
    "SOL": ["solana", "sol"], "ADA": ["cardano", "ada"],
    "XRP": ["ripple", "xrp"], "DOGE": ["dogecoin", "doge"],
    "AVAX": ["avalanche", "avax"], "DOT": ["polkadot", "dot"],
    "LINK": ["chainlink", "link"], "MATIC": ["polygon", "matic"],
    "LTC": ["litecoin", "ltc"], "BCH": ["bitcoin cash", "bch"],
}


def _http_json(url, timeout=NEWS_TIMEOUT_S):
    req = urllib.request.Request(url, headers={"User-Agent": "arena-advisor/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _pack(items):
    out = []
    for it in items[:MAX_HEADLINES]:
        ts = it.get("datetime")
        try:
            ts = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else ""
        except (TypeError, ValueError, OSError):
            ts = ""
        out.append({
            "source": f"finnhub:{it.get('source', 'news')}",
            "headline": (it.get("headline") or "")[:200],
            "body": (it.get("summary") or "")[:500],
            "ts": ts,
        })
    return out


def _stock_news(symbol):
    today = datetime.now(timezone.utc).date()
    frm = (today - timedelta(days=3)).isoformat()
    q = urllib.parse.urlencode({"symbol": symbol.upper(), "from": frm,
                                "to": today.isoformat(), "token": FINNHUB_KEY})
    data = _http_json(f"https://finnhub.io/api/v1/company-news?{q}")
    return _pack(data if isinstance(data, list) else [])


def _crypto_news(base):
    data = _http_json(
        f"https://finnhub.io/api/v1/news?category=crypto&token={FINNHUB_KEY}")
    if not isinstance(data, list):
        return []
    aliases = _CRYPTO_NAMES.get(base.upper(), [base.lower()])
    def hit(it):
        blob = f"{it.get('headline', '')} {it.get('summary', '')}".lower()
        return any(a in blob for a in aliases)
    matched = [it for it in data if hit(it)]
    # Prefer symbol-specific headlines; fall back to general crypto context.
    return _pack(matched or data)


def fetch_news(symbol):
    """Return up to MAX_HEADLINES {source,headline,body,ts} dicts for `symbol`.
    Never raises — any failure yields []."""
    if not FINNHUB_KEY:
        return []
    try:
        if "/" in symbol:                       # crypto, e.g. BTC/USD
            return _crypto_news(symbol.split("/")[0])
        return _stock_news(symbol)
    except Exception:                            # noqa: BLE001 — degrade to no-news
        return []
