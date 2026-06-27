#!/usr/bin/env python3
"""Profit-maximizing scenario sweep on the COMPOUNDED portfolio metric (what you
actually keep): start $1000, 5 equal slots, first-5-by-arm each day, compound daily,
over the fully-populated broad-cache window. Fix the proven winners (sweet arm: no
TIGHT, loc by close>200; WICK stop) and sweep the three live levers:
  - gap band (size of the gap)        - R target (let winners run)   - hold/sell-off time
Reports the top combos by final $ and the dominant pattern per lever.
"""
import os, sys, glob, itertools
from datetime import datetime, time as dtime, timedelta
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sim_variant_ibkr_days as V
C = V.C
ARM_END = dtime(9, 44); HARD_END = dtime(11, 30); OPEN_T = V.OPEN_T; OFFSET = V.OFFSET
CAP0 = 1000.0; SLOTS = 5; MAX_PRICE = 300.0

def precompute():
    """Per (sym,day): the sweet-armed candidate {gap, arm_t, entry, stop(wick), bars_after}."""
    syms = {}
    for p in glob.glob(os.path.join(V.CACHE, "*.json")):
        try: syms[os.path.basename(p)[:-5]] = V.load(p)
        except Exception: pass
    all_days = sorted({d for (_, _, _, bd) in syms.values() for d in bd})
    npresent = {d: sum(1 for (_, _, _, bd) in syms.values() if d in bd) for d in all_days}
    days = [d for d in all_days if npresent[d] >= 150]            # fully-populated window
    recs = defaultdict(list)                                      # day -> [cand,...]
    for s, (bars, s20, s200, byday) in syms.items():
        dates = sorted(byday)
        for day in days:
            if day not in byday: continue
            pos = dates.index(day)
            if pos == 0: continue
            idxs = byday[day]
            if len(idxs) < 6: continue
            pclose = bars[byday[dates[pos - 1]][-1]]["close"]; o = bars[idxs[0]]["open"]
            gap = (o - pclose) / pclose * 100
            if gap <= 0 or o > MAX_PRICE: continue
            arm = None
            for i in idxs:
                if bars[i]["dt"].time() > ARM_END: break
                if s200[i] is None: continue
                if C.bar_signal(bars[i], bars[max(0, i - 30):i]) > 0 and bars[i]["close"] > s200[i]:
                    arm = i; break
            if arm is None: continue
            entry = round(bars[arm]["high"] + OFFSET, 2); stop = round(bars[arm]["low"] - OFFSET, 2)
            if stop >= entry: continue
            after = [(bars[j]["high"], bars[j]["low"], bars[j]["close"], bars[j]["dt"])
                     for j in idxs if j > arm and bars[j]["dt"].time() <= HARD_END]
            recs[day].append({"sym": s, "gap": gap, "arm_t": bars[arm]["dt"].strftime("%H:%M"),
                              "entry": entry, "stop": stop, "after": after, "day": day})
    return days, recs

def trade_ret(c, R, hold_min):
    entry, stop = c["entry"], c["stop"]; risk = entry - stop
    target = entry + R * risk if R else None
    selloff = datetime.combine(c["day"], OPEN_T, V.ET) + timedelta(minutes=hold_min)
    in_pos = False; cur = stop; be = False
    for h, l, cl, dt in c["after"]:
        if not in_pos and h >= entry: in_pos = True
        if in_pos:
            if target and h >= target: return (target - entry) / entry * 100
            if l <= cur: return (cur - entry) / entry * 100
            if not be and h >= entry + risk: cur = entry; be = True
            if dt >= selloff: return (cl - entry) / entry * 100
    return None

def compounded(days, recs, gmin, gmax, R, hold):
    cap = CAP0
    for day in days:
        cands = [c for c in recs[day] if gmin <= c["gap"] <= gmax]
        cands.sort(key=lambda c: c["arm_t"])
        picks = cands[:SLOTS]
        if not picks: continue
        slot = cap / SLOTS
        for c in picks:
            r = trade_ret(c, R, hold)
            if r is not None: cap += slot * r / 100
    return cap

def main():
    days, recs = precompute()
    print(f"fully-populated days: {len(days)} ({days[0]}..{days[-1]})", file=sys.stderr)
    GAPS = [(0,1),(0,2),(0.5,1.5),(0.5,2),(0.5,3),(0.5,4),(1,2),(1,3),(1,4),(2,4),(0,4),(0,6),(0,100)]
    RS = [2,3,4,5,None]; HOLDS = [20,30,45,60]
    res = []
    for (gmin,gmax),R,hold in itertools.product(GAPS,RS,HOLDS):
        end = compounded(days, recs, gmin, gmax, R, hold)
        res.append({"gap":f"{gmin}-{gmax}","R":(R if R else "none"),"hold":hold,
                    "end":round(end,0),"pct":round((end/CAP0-1)*100,1)})
    res.sort(key=lambda x:-x["end"])
    print(f"\n=== TOP 15 by compounded $ (start $1000, {len(days)} days) ===")
    print(f"{'gap':9}{'R':>5}{'hold':>5}{'end$':>8}{'ret%':>8}")
    for r in res[:15]: print(f"{r['gap']:9}{str(r['R']):>5}{r['hold']:>5}{r['end']:>8}{r['pct']:>8}")
    cur=[r for r in res if r['gap']=='0.5-4' and r['R']==3 and r['hold']==30]
    if cur: print(f"\ncurrent sweet-spot (0.5-4, 3R, 30): ${cur[0]['end']} ({cur[0]['pct']:+}%)")
    print("\n=== best avg end$ per lever value ===")
    for field in ["gap","R","hold"]:
        agg=defaultdict(list)
        for r in res: agg[r[field]].append(r["end"])
        line=" | ".join(f"{k}: {round(sum(v)/len(v))}" for k,v in sorted(agg.items(),key=lambda kv:-sum(kv[1])/len(kv[1])))
        print(f"  {field:5}: {line}")

if __name__ == "__main__":
    main()
