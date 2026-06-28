#!/usr/bin/env python3
"""producer.py — the single source-of-truth signal producer (Phase 0 pilot, REPLAY mode).

Computes Tony's opening signal ONCE from cached 2-min bars (his live rule = the
baseline arm: classifier MATCH_LONG, loc-by-open, wick stop) and emits one broadcast
record per armed name, which every follower then sizes + confirms identically.

Two feeds:
  * REPLAY (--date): historical date from the broad IBKR cache; test the loop any time.
  * LIVE   (--live): reads the latest session-capture snapshot his cron already writes
    (logs/session_replay_<day>/bars_*.json) — pure file reads, ZERO CDP touch on his
    :9225 session. Faithfully reproduces his baseline arm on the real funnel.

KNOWN LIMITATION (live): a 300-bar capture carries only ~198 bars before 9:30, so
SMA200 isn't warm until ~9:34 and arms in the 9:30-9:33 window are skipped (warned at
runtime). Fix = bump session_capture's tv_bars --min to ~500 (his file), or drive his
live engine directly, for faithful open-bar signals.

Broadcast file: state/remote_confirm/broadcast/signals_<date>.json — an ordered list
(by arm time) of {broadcast_id, bar_ts, symbol, side, entry, stop, gap_pct}. Re-runs
are idempotent (broadcast_id = "<date>:<symbol>:<arm_t>").

Usage: producer.py --date 2026-06-24 [--cache <dir>] [--out <broadcast_dir>]
"""
import argparse
import glob
import json
import os
import sys
from datetime import date, datetime, time as dtime

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


SESSION_CAP = os.path.normpath(os.path.join(HERE, "..", "logs"))  # session_replay_<date>/ lives here


def build_signals_live(day):
    """LIVE mode: reuse the PROVEN dashboard assembly (sim_opening_variant) to stitch ALL
    of today's session-capture snapshots into a complete per-symbol series and run his
    baseline arm on it. Pure file reads of what his capture cron already writes — zero CDP
    touch on his :9225 session. Stitching gives the full SMA200 history (prior days +
    pre-market), so the arm list EXACTLY matches the dashboard baseline."""
    import sim_opening_variant as SOV
    cap_dir = os.path.join(SESSION_CAP, f"session_replay_{day.isoformat()}")
    if not glob.glob(os.path.join(cap_dir, "bars_*.json")):
        raise SystemExit(f"[producer] no capture snapshots in {cap_dir} "
                         f"(his session_capture cron writes these live each morning)")
    full_by = SOV.stitch(cap_dir)                      # merge every snapshot -> complete series
    out = []
    for tv, full in sorted(full_by.items()):
        if not full:
            continue
        sym = tv.split(":")[-1]
        pclose, topen, gap = SOV.premarket_gap(full, day)
        if gap is None or not (SOV.GAP_MIN <= gap <= SOV.GAP_MAX):
            continue
        if topen and (topen < V.MIN_PRICE or topen > V.MAX_PRICE):
            continue
        armed = SOV.arm_variant(full, sym, day, "baseline")   # his exact live arm
        if not armed:
            continue
        _i, entry, stop, info = armed
        out.append({
            "broadcast_id": f"{day.isoformat()}:{sym}:{info['arm_t']}",
            "bar_ts": info["arm_t"], "symbol": sym, "side": "buy",
            "entry": entry, "stop": stop, "gap_pct": round(gap, 2),
        })
    out.sort(key=lambda x: (x["bar_ts"], x["symbol"]))
    print(f"[producer] LIVE source: stitched {len(full_by)} symbols from "
          f"session_replay_{day.isoformat()}", file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="replay date YYYY-MM-DD (defaults to today in --live)")
    ap.add_argument("--live", action="store_true",
                    help="read today's live session-capture snapshot instead of the replay cache")
    ap.add_argument("--cache", default=V.CACHE, help="bar cache dir (replay mode)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="broadcast output dir")
    a = ap.parse_args()
    if a.live:
        day = date.fromisoformat(a.date) if a.date else datetime.now(V.ET).date()
        signals = build_signals_live(day)
    else:
        if not a.date:
            ap.error("--date is required in replay mode")
        day = date.fromisoformat(a.date)
        signals = build_signals(a.cache, day)
    os.makedirs(a.out, exist_ok=True)
    ds = day.isoformat()
    path = os.path.join(a.out, f"signals_{ds}.json")
    with open(path, "w") as f:
        json.dump({"date": ds, "mode": "live" if a.live else "replay",
                   "n": len(signals), "signals": signals}, f, indent=2)
    print(f"[producer] {ds} ({'LIVE' if a.live else 'replay'}): broadcast {len(signals)} armed signal(s) -> {path}")
    for sgn in signals:
        gap = "" if sgn["gap_pct"] is None else f" (gap {sgn['gap_pct']}%)"
        print(f"  {sgn['bar_ts']} {sgn['symbol']:6} buy entry {sgn['entry']} stop {sgn['stop']}{gap}")


if __name__ == "__main__":
    main()
