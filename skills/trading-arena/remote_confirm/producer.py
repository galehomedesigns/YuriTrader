#!/usr/bin/env python3
"""producer.py — the single source-of-truth signal producer (Phase 0 pilot, REPLAY mode).

Computes Tony's opening signal ONCE from cached 2-min bars (his live rule = the
baseline arm: classifier MATCH_LONG, loc-by-open, wick stop) and emits one broadcast
record per armed name, which every follower then sizes + confirms identically.

REPLAY only for now: reads a historical date from the broad IBKR cache so the whole
fan-out loop can be tested any time, touching nothing live. A live-feed mode (reading
his real-time TV session read-only) is a later step once this is proven.

Broadcast file: state/remote_confirm/broadcast/signals_<date>.json — an ordered list
(by arm time) of {broadcast_id, bar_ts, symbol, side, entry, stop, gap_pct}. Re-runs
are idempotent (broadcast_id = "<date>:<symbol>:<arm_t>").

Usage: producer.py --date 2026-06-24 [--cache <dir>] [--out <broadcast_dir>]
"""
import argparse
import json
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "opening_agent"))
import sim_variant_ibkr_days as V  # reuse: load(), sim_one(baseline), GAP/PRICE bands

DEFAULT_OUT = os.path.normpath(os.path.join(HERE, "..", "..", "..", "state", "remote_confirm", "broadcast"))


def build_signals(cache_dir, day):
    """Run the baseline (his live) arm over every cached symbol for `day`; return the
    armed signals sorted by arm time. Mirrors the per-symbol/day filter the agent uses."""
    syms = {}
    for p in __import__("glob").glob(os.path.join(cache_dir, "*.json")):
        try:
            syms[os.path.basename(p)[:-5]] = V.load(p)
        except Exception:
            pass
    out = []
    for s, (bars, s20, s200, byday) in syms.items():
        if day not in byday:
            continue
        dates = sorted(byday)
        pos = dates.index(day)
        if pos == 0:
            continue
        idxs = byday[day]
        if len(idxs) < 12:
            continue
        pclose = bars[byday[dates[pos - 1]][-1]]["close"]
        o = bars[idxs[0]]["open"]
        gap = (o - pclose) / pclose * 100
        if not (V.GAP_MIN <= gap <= V.GAP_MAX) or o > V.MAX_PRICE or o < V.MIN_PRICE:
            continue
        r = V.sim_one(bars, s20, s200, idxs, day, s, "baseline")  # his live arm rule
        if not r:
            continue  # never armed
        out.append({
            "broadcast_id": f"{day.isoformat()}:{s}:{r['arm_t']}",
            "bar_ts": r["arm_t"], "symbol": s, "side": "buy",
            "entry": r["entry"], "stop": r["stop"], "gap_pct": round(gap, 2),
        })
    out.sort(key=lambda x: (x["bar_ts"], x["symbol"]))  # arm order = his first-to-arm
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="replay date YYYY-MM-DD")
    ap.add_argument("--cache", default=V.CACHE, help="bar cache dir (default broad IBKR cache)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="broadcast output dir")
    a = ap.parse_args()
    day = date.fromisoformat(a.date)
    signals = build_signals(a.cache, day)
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"signals_{a.date}.json")
    with open(path, "w") as f:
        json.dump({"date": a.date, "mode": "replay", "n": len(signals), "signals": signals}, f, indent=2)
    print(f"[producer] {a.date}: broadcast {len(signals)} armed signal(s) -> {path}")
    for sgn in signals:
        print(f"  {sgn['bar_ts']} {sgn['symbol']:6} buy entry {sgn['entry']} stop {sgn['stop']} (gap {sgn['gap_pct']}%)")


if __name__ == "__main__":
    main()
