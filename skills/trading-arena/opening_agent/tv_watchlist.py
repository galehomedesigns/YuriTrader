#!/usr/bin/env python3
"""TradingView watchlist sync — replace the default watchlist with a set of
tickers so they sync to all the user's TradingView devices (phone, desktop).

Uses the account session cookie (TRADINGVIEW_SESSIONID in .env) to call
TradingView's private REST API directly — NO browser / CDP needed.

  GET  /api/v1/symbols_list/all/                 -> find the active list id
  POST /api/v1/symbols_list/custom/<id>/replace/ -> set its symbols
  symbol-search.tradingview.com/symbol_search/   -> plain ticker -> EXCHANGE:TICKER

This account is free-tier, so TradingView allows editing only the one default
watchlist. Per the user's 2026-06-13 decision we REPLACE it entirely each run.
The pre-integration original is backed up in logs/tv_watchlist_backups/.

CLI:
    python3 tv_watchlist.py AMD GOOGL SMCI       # resolve + replace
    python3 tv_watchlist.py --dry-run AMD GOOGL  # resolve + print, no write
    python3 tv_watchlist.py --from-cache         # use opening_scan_latest.json ranked[]
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

_HERE = os.path.dirname(os.path.abspath(__file__))
_LOGS = os.path.join(os.path.dirname(_HERE), "logs")


def _load_env():
    p = "/home/tonygale/openclaw/.env"
    if not os.path.exists(p):
        return
    for line in open(p):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            if k and v:
                os.environ.setdefault(k, v)


_load_env()

SESSIONID = os.environ.get("TRADINGVIEW_SESSIONID", "")
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/145.0.0.0 Safari/537.36")
# US exchanges we trust for a plain ticker, best-first (opening agent is US equities).
_US_EXCHANGES = ["NASDAQ", "NYSE", "AMEX", "NYSE ARCA", "BATS", "CBOE", "OTC"]
_TAG_RE = re.compile(r"<[^>]+>")


class TVAuthError(RuntimeError):
    """Raised when the session cookie is missing or rejected (login required)."""


def _api_request(url, data=None, method=None, timeout=20):
    headers = {
        "Cookie": f"sessionid={SESSIONID}",
        "User-Agent": _UA,
        "Referer": "https://www.tradingview.com/chart/",
        "Origin": "https://www.tradingview.com",
        "x-requested-with": "XMLHttpRequest",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode()
            return r.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if e.code in (401, 403) and "login" in body.lower():
            raise TVAuthError(
                "TradingView rejected the session cookie (login_required). "
                "The TRADINGVIEW_SESSIONID in .env has expired — grab a fresh "
                "'sessionid' cookie from a logged-in tradingview.com browser tab."
            ) from e
        return e.code, body


def resolve_symbol(ticker, _cache={}):
    """Plain ticker (e.g. 'AMD') -> TradingView symbol (e.g. 'NASDAQ:AMD').

    Falls back to the plain ticker if symbol-search returns nothing usable.
    """
    t = ticker.strip().upper()
    if not t or t.startswith("###"):
        return ticker  # pass section headers through untouched
    if t in _cache:
        return _cache[t]
    url = ("https://symbol-search.tradingview.com/symbol_search/?"
           + urllib.parse.urlencode({"text": t, "type": "stock", "hl": "0", "lang": "en"}))
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA, "Origin": "https://www.tradingview.com",
            "Referer": "https://www.tradingview.com/",
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            payload = json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001 — network/parse; fall back to plain
        print(f"  [tv-wl] symbol-search failed for {t}: {e}", file=sys.stderr)
        _cache[t] = t
        return t

    rows = payload if isinstance(payload, list) else payload.get("symbols", [])
    # Strip highlight tags; keep only exact-ticker stock matches.
    cands = []
    for row in rows:
        sym = _TAG_RE.sub("", row.get("symbol", "")).strip().upper()
        if sym != t:
            continue
        cands.append((row.get("exchange", "").strip(), sym))
    chosen = None
    for ex in _US_EXCHANGES:                       # prefer US listings, best-first
        for exch, sym in cands:
            if exch == ex:
                chosen = f"{exch}:{sym}"
                break
        if chosen:
            break
    if not chosen and cands:                       # any exact match
        exch, sym = cands[0]
        chosen = f"{exch}:{sym}" if exch else sym
    result = chosen or t                           # last resort: plain ticker
    _cache[t] = result
    return result


def get_active_list():
    """Return (list_id, name) of the active/default watchlist, or (None, None)."""
    if not SESSIONID:
        raise TVAuthError("TRADINGVIEW_SESSIONID is not set in .env — cannot sync.")
    status, body = _api_request("https://www.tradingview.com/api/v1/symbols_list/all/")
    if status != 200:
        raise TVAuthError(f"Could not list watchlists (HTTP {status}): {body[:160]}")
    lists = json.loads(body)
    if not lists:
        return None, None
    active = next((x for x in lists if x.get("active")), lists[0])
    return active.get("id"), active.get("name")


def replace_watchlist(symbols, list_id):
    """Replace the symbols of the given watchlist. Returns the stored list."""
    # unsafe=true is required to ADD symbols; the default "safe" replace only
    # permits reordering existing ones (HTTP 422 add_new_symbols otherwise).
    url = (f"https://www.tradingview.com/api/v1/symbols_list/custom/"
           f"{list_id}/replace/?unsafe=true")
    status, body = _api_request(url, data=symbols, method="POST")
    if status != 200:
        raise RuntimeError(f"replace failed (HTTP {status}): {body[:200]}")
    return json.loads(body)


def sync(tickers, dry_run=False, header=True, label="Opening Power"):
    """Resolve `tickers` and replace the default watchlist with them.

    `label` names the leading ###section header (e.g. "Opening Power" for the
    pre-market top-10, "MATCHES" for the 9:32 first-bar signals).
    Returns a dict {ok, list_id, name, sent:[...], resolved:{ticker:tvsym}}.
    """
    seen, ordered = set(), []
    for t in tickers:
        u = t.strip().upper()
        if u and u not in seen:
            seen.add(u)
            ordered.append(u)

    resolved = {t: resolve_symbol(t) for t in ordered}
    payload = list(dict.fromkeys(resolved[t] for t in ordered))  # dedupe, keep order
    if header:
        et = datetime.now(ZoneInfo("America/New_York")).strftime("%m-%d %H:%M")
        payload = [f"###{label} {et}"] + payload

    if dry_run:
        print(f"[tv-wl] DRY-RUN — would set {len(payload)} entries: {payload}")
        return {"ok": True, "dry_run": True, "sent": payload, "resolved": resolved}

    list_id, name = get_active_list()
    if not list_id:
        print("[tv-wl] no watchlist found on account", file=sys.stderr)
        return {"ok": False, "sent": payload, "resolved": resolved}
    stored = replace_watchlist(payload, list_id)
    print(f"[tv-wl] replaced '{name}' (id {list_id}) with {len(payload)} entries")
    return {"ok": True, "list_id": list_id, "name": name,
            "sent": payload, "stored": stored, "resolved": resolved}


def _tickers_from_cache():
    cache = os.environ.get("OPENING_SCAN_CACHE",
                           os.path.join(_LOGS, "opening_scan_latest.json"))
    with open(cache) as f:
        return [r["symbol"] for r in json.load(f).get("ranked", [])]


def main():
    ap = argparse.ArgumentParser(description="Replace the TradingView watchlist.")
    ap.add_argument("tickers", nargs="*", help="plain tickers, e.g. AMD GOOGL")
    ap.add_argument("--from-cache", action="store_true",
                    help="read tickers from opening_scan_latest.json ranked[]")
    ap.add_argument("--dry-run", action="store_true", help="resolve + print, no write")
    ap.add_argument("--no-header", action="store_true", help="omit the ### section header")
    a = ap.parse_args()

    tickers = _tickers_from_cache() if a.from_cache else a.tickers
    if not tickers:
        ap.error("no tickers given (pass tickers or --from-cache)")
    try:
        sync(tickers, dry_run=a.dry_run, header=not a.no_header)
    except (TVAuthError, RuntimeError) as e:
        print(f"[tv-wl] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
