#!/usr/bin/env python3
"""ISOLATED IBKR 2-min history backfill — backtest data only.

Pulls native 2-minute RTH bars from the IB Gateway and writes them into a
SEPARATE cache dir (logs/backtest_cache_ibkr/) in the exact format the backtest
engine reads, so backtest_full.py can run over multi-year history via:

    OPENING_BT_CACHE_DIR=.../logs/backtest_cache_ibkr OPENING_BT_CACHE_ANY=1 \
        python3 backtest_full.py --premarket-gap --rank-topn --news --cache-only

Kept completely apart from the live path: imports nothing from opening_agent /
tv_* / questrade / CDP, dedicated clientId, read-only (reqHistoricalData only),
and its own cache dir — the Questrade cache is never touched.

IBKR realities (probed 2026-06-21): native 2-min bars back 3+ years; "1 Y" in one
request is rejected, so we page in monthly chunks (~4000 bars each); historical
works on the delayed/unsubscribed account. Pacing: <=60 requests / 10 min, so we
sleep between requests. Resumable — a symbol already cached deep enough is skipped.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

from ib_async import IB, Stock

ET = ZoneInfo("America/New_York")
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.environ.get("IBKR_BACKFILL_CACHE_DIR") or os.path.join(os.path.dirname(HERE), "logs", "backtest_cache_ibkr")
OPEN_T, RTH_END = dtime(9, 30), dtime(16, 0)

HOST = os.environ.get("IBKR_HOST", "127.0.0.1")
PORT = int(os.environ.get("IBKR_PORT", "4001"))
CLIENT_ID = int(os.environ.get("IBKR_BACKFILL_CLIENT_ID", "88"))

# Liquid, frequently-gapping US names the opening strategy would actually scan.
DEFAULT_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD", "NFLX", "AVGO",
    "MU", "QCOM", "CRM", "ORCL", "ADBE", "INTC", "CSCO", "PLTR", "SOFI", "COIN",
    "MARA", "RIOT", "F", "GM", "BAC", "JPM", "WFC", "XOM", "CVX", "BABA",
    "UBER", "ABNB", "DKNG", "SNAP", "PINS", "ROKU", "DIS", "BA", "CAT", "WMT",
    "TGT", "COST", "HOOD", "SHOP", "NIO", "RIVN", "LCID", "GME", "SMCI", "WBD",
]


def _et(b_date):
    """IBKR intraday bar.date -> ET-aware datetime (handles datetime or string)."""
    if isinstance(b_date, datetime):
        dt = b_date
    else:
        s = str(b_date)
        try:
            dt = datetime.strptime(s.split(" US/")[0].strip(), "%Y%m%d %H:%M:%S")
        except ValueError:
            dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    return dt.astimezone(ET)


def cache_path(sym):
    return os.path.join(CACHE_DIR, f"{sym}.json")


def already_deep_enough(sym, target_start):
    p = cache_path(sym)
    if not os.path.exists(p):
        return False
    try:
        d = json.load(open(p))
        return d.get("from") and d["from"] <= target_start and d.get("bars")
    except (OSError, ValueError):
        return False


def fetch_symbol(ib, sym, months, sleep_s):
    """Walk back `months` monthly chunks of 2-min RTH bars; write the merged cache."""
    c = Stock(sym, "SMART", "USD")
    try:
        ib.qualifyContracts(c)
    except Exception as e:                                       # noqa: BLE001
        print(f"  [{sym}] qualify FAIL: {e}", file=sys.stderr)
        return 0
    seen, bars = set(), []
    end = ""                                                     # "" = now
    for m in range(months):
        try:
            chunk = ib.reqHistoricalData(c, endDateTime=end, durationStr="1 M",
                                         barSizeSetting="2 mins", whatToShow="TRADES",
                                         useRTH=True, formatDate=1, timeout=90)
        except Exception as e:                                   # noqa: BLE001
            print(f"  [{sym}] chunk {m} error: {e}; backing off 30s", file=sys.stderr)
            time.sleep(30)
            continue
        if not chunk:
            break                                               # ran out of history
        for b in chunk:
            dt = _et(b.date)
            if not (OPEN_T <= dt.time() < RTH_END):
                continue
            k = dt.isoformat()
            if k in seen:
                continue
            seen.add(k)
            bars.append({"et": k, "open": float(b.open), "high": float(b.high),
                         "low": float(b.low), "close": float(b.close),
                         "volume": float(b.volume or 0)})
        # next chunk ends just before this chunk's oldest bar
        oldest = _et(chunk[0].date)
        end = oldest.strftime("%Y%m%d %H:%M:%S US/Eastern")
        time.sleep(sleep_s)                                     # IBKR pacing
    if not bars:
        return 0
    bars.sort(key=lambda b: b["et"])
    os.makedirs(CACHE_DIR, exist_ok=True)
    json.dump({"source": "ibkr", "from": bars[0]["et"][:10], "through": bars[-1]["et"][:10],
               "bars": bars}, open(cache_path(sym), "w"))
    return len(bars)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="comma list (default: built-in liquid set)")
    ap.add_argument("--months", type=int, default=24, help="how many monthly chunks back")
    ap.add_argument("--sleep", type=float, default=11.0, help="seconds between requests (pacing)")
    ap.add_argument("--force", action="store_true", help="refetch even if already deep enough")
    args = ap.parse_args()

    syms = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            or DEFAULT_SYMBOLS)
    target_start = (datetime.now(ET) - timedelta(days=args.months * 30 + 5)).date().isoformat()

    ib = IB()
    ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=25)
    ib.reqMarketDataType(3)                                     # delayed account
    print(f"[ibkr-backfill] connected={ib.isConnected()} acct={ib.managedAccounts()} "
          f"| {len(syms)} symbols x {args.months}mo, sleep {args.sleep}s, cache={CACHE_DIR}")
    done = skipped = 0
    for i, sym in enumerate(syms, 1):
        if not args.force and already_deep_enough(sym, target_start):
            skipped += 1
            print(f"[{i}/{len(syms)}] {sym}: already cached to {target_start} — skip")
            continue
        n = fetch_symbol(ib, sym, args.months, args.sleep)
        done += 1
        print(f"[{i}/{len(syms)}] {sym}: {n} bars cached", flush=True)
    ib.disconnect()
    print(f"[ibkr-backfill] DONE — {done} fetched, {skipped} already-cached, {len(syms)} total")


if __name__ == "__main__":
    main()
