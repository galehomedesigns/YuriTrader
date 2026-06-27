#!/usr/bin/env python3
"""Test a Relative-Volume (RVOL) filter on the improved config (gap 2-4%, 3R, 45-min).
RVOL = arm-bar volume / average volume at that SAME minute over the prior K days
(true time-of-day relative volume). Filter candidates by RVOL >= threshold and see
if it lifts the flat first half. Compounded $1000/5, first-5-by-arm, daily."""
import os, sys, json, glob
from datetime import datetime, time as dtime, timedelta
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sim_variant_ibkr_days as V
C = V.C
ARM_END = dtime(9, 44); HARD_END = dtime(11, 30); OPEN_T = V.OPEN_T; OFFSET = V.OFFSET
CAP0 = 1000.0; SLOTS = 5; MAX_PRICE = 300.0; KDAYS = 14
GMIN, GMAX, R, HOLD = 2.0, 4.0, 3, 45        # the improved config

def load_vol(path):
    d = json.load(open(path)); bars = []
    for b in d.get("bars", []):
        dt = datetime.fromisoformat(b["et"])
        if OPEN_T <= dt.time() <= dtime(16, 0):
            bars.append({"dt": dt, "open": b["open"], "high": b["high"], "low": b["low"],
                         "close": b["close"], "vol": b.get("volume", 0) or 0})
    bars.sort(key=lambda x: x["dt"])
    closes = [x["close"] for x in bars]
    s20, s200 = V.roll(closes, 20), V.roll(closes, 200)
    byday = defaultdict(list)
    tod = defaultdict(list)                                  # "HH:MM" -> [(date, vol), ...] in order
    for i, x in enumerate(bars):
        byday[x["dt"].date()].append(i)
        tod[x["dt"].strftime("%H:%M")].append((x["dt"].date(), x["vol"]))
    return bars, s20, s200, byday, tod

def precompute():
    syms = {}
    for p in glob.glob(os.path.join(V.CACHE, "*.json")):
        try: syms[os.path.basename(p)[:-5]] = load_vol(p)
        except Exception: pass
    all_days = sorted({d for (_, _, _, bd, _) in syms.values() for d in bd})
    npres = {d: sum(1 for (_, _, _, bd, _) in syms.values() if d in bd) for d in all_days}
    days = [d for d in all_days if npres[d] >= 150]
    recs = defaultdict(list)
    for s, (bars, s20, s200, byday, tod) in syms.items():
        dates = sorted(byday)
        for day in days:
            if day not in byday: continue
            pos = dates.index(day)
            if pos == 0: continue
            idxs = byday[day]
            if len(idxs) < 6: continue
            pclose = bars[byday[dates[pos - 1]][-1]]["close"]; o = bars[idxs[0]]["open"]
            gap = (o - pclose) / pclose * 100
            if not (GMIN <= gap <= GMAX) or o > MAX_PRICE: continue
            arm = None
            for i in idxs:
                if bars[i]["dt"].time() > ARM_END: break
                if s200[i] is None: continue
                if C.bar_signal(bars[i], bars[max(0, i - 30):i]) > 0 and bars[i]["close"] > s200[i]:
                    arm = i; break
            if arm is None: continue
            entry = round(bars[arm]["high"] + OFFSET, 2); stop = round(bars[arm]["low"] - OFFSET, 2)
            if stop >= entry: continue
            # RVOL: arm-bar vol vs avg vol at same minute over prior K days
            t = bars[arm]["dt"].strftime("%H:%M")
            hist = [v for (dd, v) in tod[t] if dd < day][-KDAYS:]
            rvol = (bars[arm]["vol"] / (sum(hist) / len(hist))) if len(hist) >= 3 and sum(hist) > 0 else None
            after = [(bars[j]["high"], bars[j]["low"], bars[j]["close"], bars[j]["dt"])
                     for j in idxs if j > arm and bars[j]["dt"].time() <= HARD_END]
            recs[day].append({"sym": s, "gap": gap, "arm_t": t, "entry": entry, "stop": stop,
                              "rvol": rvol, "after": after, "day": day})
    return days, recs

def trade_ret(c):
    entry, stop = c["entry"], c["stop"]; risk = entry - stop; target = entry + R * risk
    selloff = datetime.combine(c["day"], OPEN_T, V.ET) + timedelta(minutes=HOLD)
    in_pos = False; cur = stop; be = False
    for h, l, cl, dt in c["after"]:
        if not in_pos and h >= entry: in_pos = True
        if in_pos:
            if h >= target: return (target - entry) / entry * 100
            if l <= cur: return (cur - entry) / entry * 100
            if not be and h >= entry + risk: cur = entry; be = True
            if dt >= selloff: return (cl - entry) / entry * 100
    return None

def compounded(days, recs, rvol_min):
    cap = CAP0; ntr = 0
    for day in days:
        cands = [c for c in recs[day] if (rvol_min == 0 or (c["rvol"] is not None and c["rvol"] >= rvol_min))]
        cands.sort(key=lambda c: c["arm_t"])
        for c in cands[:SLOTS]:
            r = trade_ret(c)
            if r is not None: cap += (cap / SLOTS) * r / 100; ntr += 1
    return cap, ntr

def main():
    days, recs = precompute()
    half = len(days) // 2
    segs = {"first-half": days[:half], "second-half": days[half:], "all": days}
    print(f"improved config gap{GMIN}-{GMAX}/{R}R/{HOLD}min · {len(days)} days · RVOL=vol vs same-minute avg over {KDAYS}d\n", file=sys.stderr)
    print(f"{'RVOL min':10}" + "".join(f"{k:>22}" for k in segs))
    for rv in [0, 1.0, 1.5, 2.0, 3.0]:
        row = f"{('none' if rv==0 else str(rv)):10}"
        for seg in segs.values():
            end, ntr = compounded(seg, recs, rv)
            row += f"{('$%d (%+.1f%%) %dtr'%(end,(end/CAP0-1)*100,ntr)):>22}"
        print(row)

if __name__ == "__main__":
    main()
