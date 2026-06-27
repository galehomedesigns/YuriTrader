#!/usr/bin/env python3
"""Daily opening-session 2-min bar capturer — feeds the variant/sim dashboards.

Cron launches this once at ~9:28 ET. It reads the morning's pre-market funnel
(opening_scan_latest.json), resolves each symbol to EXCHANGE:SYMBOL via
tv_symbol_cache.json, then snapshots 2-min OHLC for the whole funnel every 120s
into logs/session_replay_<YYYY-MM-DD>/bars_<HHMMSS>.json until 12:00 ET. Each
tv_bars_fetch pull returns 300 historical bars, so the first snapshot already
contains the 9:30 opening bars even if capture starts a minute late.

Needs the trading Chrome on CDP :9225 (same session the agent uses). No orders.
"""
import os, sys, json, time, subprocess
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ET = ZoneInfo("America/New_York")
LOGS = os.path.join(HERE, "..", "logs")
SCAN = os.path.join(LOGS, "opening_scan_latest.json")
SYMCACHE = os.path.join(LOGS, "tv_symbol_cache.json")
TOP_N = 20            # capture the top-N ranked funnel names
STOP_AT = dtime(12, 0)   # stop capturing at 12:00 ET
INTERVAL = 120


def resolve_symbols():
    ranked = json.load(open(SCAN)).get("ranked", [])
    cache = json.load(open(SYMCACHE)) if os.path.exists(SYMCACHE) else {}
    out = []
    for r in ranked[:TOP_N]:
        sym = r.get("symbol")
        tv = cache.get(sym)
        if tv:
            out.append(tv)
        else:
            print(f"[capture] no exchange for {sym} — skipped", file=sys.stderr)
    return out


def main():
    now = datetime.now(ET)
    day = now.strftime("%Y-%m-%d")
    d = os.path.join(LOGS, f"session_replay_{day}")
    os.makedirs(d, exist_ok=True)
    syms = resolve_symbols()
    if not syms:
        print("[capture] no symbols resolved — abort", file=sys.stderr); return
    symarg = ",".join(syms)
    print(f"[capture] {day}: {len(syms)} symbols -> {d}")
    while True:
        et = datetime.now(ET)
        stamp = et.strftime("%H%M%S")
        out = os.path.join(d, f"bars_{stamp}.json")
        try:
            with open(out, "w") as f:
                subprocess.run(["node", os.path.join(HERE, "tv_bars_fetch.js"),
                                "--symbols", symarg, "--min", "300", "--res", "2", "--port", "9225"],
                               stdout=f, stderr=subprocess.DEVNULL, timeout=110)
            print(f"[capture] {stamp} ET -> {os.path.basename(out)}")
        except Exception as e:
            print(f"[capture] {stamp} fetch error: {e}", file=sys.stderr)
        if et.time() >= STOP_AT:
            print(f"[capture] reached {STOP_AT}, stopping"); break
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
