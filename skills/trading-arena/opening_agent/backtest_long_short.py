#!/usr/bin/env python3
"""Long vs SHORT edge test for Opening-Power over the IBKR 2-yr cache.

The live system is long-only, but classify_opening already emits MATCH_SHORT (bear
power-bar + location below the 20/200 band). This scores BOTH sides with the REAL
classifier + REAL entry/stop levels (mirrored for shorts), a naive exit (initial stop
or the 30-min cutoff close), and realistic price-scaled slippage. Reports long-only,
short-only, and combined — with an IS/OOS split + per-symbol breadth so a fluke shows.

Reads ONLY the IBKR cache. No network, no live system, places nothing.
"""
import json
import os
import sys
import statistics as st
from collections import defaultdict
from datetime import datetime, time
from zoneinfo import ZoneInfo

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from opening_agent import classifier as C            # noqa: E402
import shared.indicators as ind                      # noqa: E402

ET = ZoneInfo("America/New_York")
LOGS = os.path.join(os.path.dirname(_HERE), "logs")
CACHE = os.path.join(LOGS, os.environ.get("LS_CACHE_DIR", "backtest_cache_ibkr_tech"))
OPEN_T = time(9, 30)
CUTOFF_MIN = int(os.environ.get("OPENING_SESSION_CUTOFF_MIN", "30"))
SLIP_PCT = float(os.environ.get("OPENING_BT_SLIP_PCT", "0.0010"))
SLIP_CENTS = float(os.environ.get("OPENING_BT_SLIP_CENTS", "0.02"))
WARMUP = 200


def _slip(px):
    return max(SLIP_PCT, SLIP_CENTS / px if px > 0 else 0.0)


def load(sym):
    raw = json.load(open(os.path.join(CACHE, sym + ".json")))
    bars = [{"et": datetime.fromisoformat(b["et"]), "open": b["open"], "high": b["high"],
             "low": b["low"], "close": b["close"]} for b in raw.get("bars", [])]
    bars.sort(key=lambda x: x["et"])
    return bars


def naive_sim(side, bar1, session, cutoff_ts):
    """side +1 long / -1 short. Entry = takeout of the bar-1 high (long) / low (short);
    exit = the mirrored initial stop, else the cutoff close. Returns ret% or None."""
    entry_lvl = C.entry_level_long(bar1) if side > 0 else C.entry_level_short(bar1)
    stop_lvl = C.stop_level_long(bar1) if side > 0 else C.stop_level_short(bar1)
    window = [b for b in session if b["et"].timestamp() <= cutoff_ts + 1]
    entered = exit_px = None
    for b in window[1:]:
        if entered is None:
            took = (b["high"] >= entry_lvl) if side > 0 else (b["low"] <= entry_lvl)
            if took:
                entered = entry_lvl * (1 + _slip(entry_lvl)) if side > 0 else entry_lvl * (1 - _slip(entry_lvl))
            continue
        hit = (b["low"] <= stop_lvl) if side > 0 else (b["high"] >= stop_lvl)
        if hit:
            exit_px = stop_lvl * (1 - _slip(stop_lvl)) if side > 0 else stop_lvl * (1 + _slip(stop_lvl))
            break
    if entered is None:
        return None
    if exit_px is None:
        last = window[-1]["close"]
        exit_px = last * (1 - _slip(last)) if side > 0 else last * (1 + _slip(last))
    return ((exit_px - entered) / entered * 100) if side > 0 else ((entered - exit_px) / entered * 100)


def run():
    files = [f[:-5] for f in os.listdir(CACHE) if f.endswith(".json") and not f.startswith("_")]
    rows = []   # (date, symbol, side_label, ret)
    for sym in files:
        try:
            bars = load(sym)
        except Exception:                              # noqa: BLE001
            continue
        closes = [b["close"] for b in bars]
        byday = defaultdict(list)
        for i, b in enumerate(bars):
            byday[b["et"].date()].append(i)
        for d in sorted(byday):
            idxs = byday[d]
            oi = next((i for i in idxs if bars[i]["et"].time() >= OPEN_T), None)
            if oi is None or oi < WARMUP:
                continue
            bar1 = bars[oi]
            prior = bars[:oi]
            smf, sms = ind.sma(closes[:oi + 1], 20), ind.sma(closes[:oi + 1], 200)
            v = C.classify_opening(sym, bar1, prior, smf, sms)
            side = 1 if v.decision == "MATCH_LONG" else -1 if v.decision == "MATCH_SHORT" else 0
            if side == 0:
                continue
            cutoff_ts = datetime.combine(d, OPEN_T, ET).timestamp() + CUTOFF_MIN * 60
            session = bars[oi:]
            ret = naive_sim(side, bar1, session, cutoff_ts)
            if ret is not None:
                rows.append((str(d), sym, "LONG" if side > 0 else "SHORT", round(ret, 3)))
    return rows


def summ(sel):
    if not sel:
        return None
    days = sorted({r[0] for r in sel})
    cut = days[len(days) // 2]
    isr = [r[3] for r in sel if r[0] < cut]
    oos = [r[3] for r in sel if r[0] >= cut]
    bysym = defaultdict(list)
    for r in sel:
        bysym[r[1]].append(r[3])
    pos_syms = sum(1 for v in bysym.values() if sum(v) > 0)
    wins = sum(1 for r in sel if r[3] > 0)
    m = lambda g: st.mean(g) if g else 0.0
    return {"n": len(sel), "win": round(100 * wins / len(sel), 1), "avg": round(m([r[3] for r in sel]), 3),
            "tot": round(sum(r[3] for r in sel), 1), "is": round(m(isr), 3), "oos": round(m(oos), 3),
            "sym": f"{pos_syms}/{len(bysym)}"}


def main():
    rows = run()
    print(f"[ls] {len([f for f in os.listdir(CACHE) if f.endswith('.json')])} symbols | "
          f"{len(rows)} triggered trades | slip=max({SLIP_PCT*100:.2f}%, {SLIP_CENTS*100:.0f}c/px)", file=sys.stderr)
    days = sorted({r[0] for r in rows})
    if days:
        print(f"window {days[0]} -> {days[-1]} | IS/OOS split {days[len(days)//2]}")
    print(f"\n{'side':<10}{'n':>6}{'win%':>7}{'avg%':>8}{'IS%':>8}{'OOS%':>8}{'total%':>9}{'sym+':>9}")
    print("-" * 65)
    for lab, sel in (("LONG", [r for r in rows if r[2] == "LONG"]),
                     ("SHORT", [r for r in rows if r[2] == "SHORT"]),
                     ("BOTH", rows)):
        s = summ(sel)
        if s:
            print(f"{lab:<10}{s['n']:>6}{s['win']:>7}{s['avg']:>8.3f}{s['is']:>8.3f}"
                  f"{s['oos']:>8.3f}{s['tot']:>9.1f}{s['sym']:>9}")


if __name__ == "__main__":
    main()
