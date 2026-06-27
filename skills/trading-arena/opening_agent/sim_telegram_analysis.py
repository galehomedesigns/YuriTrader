#!/usr/bin/env python3
"""Full analysis of the REAL Telegram funnel picks we have bars for, split into the
two researchable parts:
  PART A — PASSED the 2-minute test (armed): the real verified trades (45-min config).
  PART B — FAILED the 2-minute test (in cache, didn't arm): trade-anyway counterfactual
           (buy 9:30 open, hold 45-min) to see whether the 2-min gate actually helped.
Reads the parsed picks from logs/opening_sim_variant.json (telegram block) + the IBKR
cache for bars. Long-only. Prints day-by-day + aggregates."""
import os, sys, json, glob
from datetime import date, time as dtime, datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sim_variant_ibkr_days as V
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "opening_sim_variant.json")
OPEN_T = V.OPEN_T; HOLD = 45; CAP0, SLOTS = 1000.0, 5

def naive_open_hold(bars, idxs, day):
    """Buy the 9:30 open, sell at the +45-min bar close (long-only, no gate/stop)."""
    if not idxs: return None
    entry = bars[idxs[0]]["open"]
    selloff = datetime.combine(day, OPEN_T, V.ET) + timedelta(minutes=HOLD)
    exitpx = bars[idxs[0]]["close"]
    for i in idxs:
        exitpx = bars[i]["close"]
        if bars[i]["dt"] >= selloff: break
    return (exitpx - entry) / entry * 100 if entry else None

def main():
    tel = json.load(open(OUT))["telegram"]
    TGCACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "telegram_cache")
    cache = {}
    for cdir in (V.CACHE, TGCACHE):
        for p in glob.glob(os.path.join(cdir, "*.json")):
            try: cache[os.path.basename(p)[:-5]] = V.load(p)
            except Exception: pass

    passed = []      # (day, sym, gap, ret)  — armed (passed 2-min)
    failed = []      # (day, sym, gap, naive_ret) — in cache, did NOT arm
    for d in tel["days"]:
        day = date.fromisoformat(d["day"])
        for pk in d["picks"]:
            if not pk["in_cache"]: continue
            if pk["armed"]:
                passed.append((d["day"], pk["sym"], pk.get("gap"), pk["ret_pct"]))
            else:
                bars, s20, s200, byday = cache[pk["sym"]]
                if day in byday:
                    nr = naive_open_hold(bars, byday[day], day)
                    if nr is not None: failed.append((d["day"], pk["sym"], pk.get("gap"), round(nr, 3)))

    def stats(rows):
        r = [x[3] for x in rows]
        if not r: return None
        w = [x for x in r if x > 0]
        return dict(n=len(r), win=round(len(w)/len(r)*100, 1), avg=round(sum(r)/len(r), 3),
                    tot=round(sum(r), 1), best=round(max(r), 1), worst=round(min(r), 1))

    sp, sf = stats(passed), stats(failed)
    print("="*64)
    print("PART A — picks that PASSED the 2-minute test (armed, 45-min trade):")
    print(f"  {sp}")
    # compounded $1000/5 over passed picks, by day
    cap = CAP0; byday = {}
    for dd, sym, gap, ret in passed: byday.setdefault(dd, []).append((sym, gap, ret))
    print(f"\n  day-by-day (compounded $1000, 5 slots):")
    for dd in sorted(byday):
        picks = byday[dd][:SLOTS]; slot = cap/SLOTS
        for sym, gap, ret in picks: cap += slot*ret/100
        names = ", ".join(f"{s}{('%+.1f%%'%r)}" for s, g, r in picks)
        print(f"    {dd}: {names}  -> ${cap:.0f}")
    print(f"  FINAL: $1000 -> ${cap:.0f} ({(cap/CAP0-1)*100:+.1f}%)")
    print("\n" + "="*64)
    print("PART B — picks that FAILED the 2-minute test (in cache), traded ANYWAY (buy open, 45-min):")
    print(f"  {sf}")
    print("\n" + "="*64)
    print("DID THE 2-MINUTE TEST HELP?  (avg return per pick)")
    print(f"  passed-the-gate (gated rules): {sp['avg'] if sp else 'n/a'}%   over {sp['n'] if sp else 0} picks")
    print(f"  failed-the-gate (traded anyway): {sf['avg'] if sf else 'n/a'}%   over {sf['n'] if sf else 0} picks")
    if sp and sf:
        verdict = "the gate ADDED value" if sp['avg'] > sf['avg'] else "the gate did NOT add value"
        print(f"  => {verdict} ({sp['avg']:+.3f}% gated vs {sf['avg']:+.3f}% ungated)")

if __name__ == "__main__":
    main()
