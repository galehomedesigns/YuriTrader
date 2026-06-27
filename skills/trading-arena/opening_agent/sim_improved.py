#!/usr/bin/env python3
"""Improved-config dashboard data: gap 2-4% · 3R · 45-min hold · wick stop · loc-by-close.
Two setups, side by side: IMPROVED (no filter) vs IMPROVED + RVOL>=1.0 (arm-bar volume vs
same-minute avg over prior 14 days). Writes logs/opening_sim_improved.json for the shared
opening-sim-multi template. IBKR broad cache, compounded $1000/5 first-5-by-arm."""
import os, sys, json, glob
from datetime import datetime, time as dtime
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sim_variant_ibkr_days as V
C = V.C
# set the improved knobs (sim_one reads these module globals on V)
V.GAP_MIN, V.GAP_MAX, V.RR, V.SELLOFF_MIN = 2.0, 4.0, 3.0, 45
ARM_END = dtime(9, 44); OPEN_T = V.OPEN_T; OFFSET = V.OFFSET
CAP0 = 1000.0; SLOTS = 5; MAX_PRICE = 300.0; KDAYS = 14; RVOL_MIN = 1.0
N_DAYS = 22

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
    byday = defaultdict(list); tod = defaultdict(list)
    for i, x in enumerate(bars):
        byday[x["dt"].date()].append(i); tod[x["dt"].strftime("%H:%M")].append((x["dt"].date(), x["vol"]))
    return bars, s20, s200, byday, tod

def compound(days, key):
    cur = CAP0; curve = []
    for d in sorted(days, key=lambda x: x["day"]):
        picks = sorted(d[key]["picks"], key=lambda p: (p.get("arm_t") or "99:99"))[:SLOTS]
        slot = cur / SLOTS
        rows = [{"sym": p["sym"], "gap_pct": p.get("gap_pct"), "ret_pct": p["ret_pct"],
                 "ret_usd": round(slot * p["ret_pct"] / 100, 2)} for p in picks]
        day_pl = round(sum(x["ret_usd"] for x in rows), 2)
        prev = cur; cur = round(cur + day_pl, 2)
        curve.append({"day": d["day"], "slot": round(slot, 2), "n": len(rows), "picks": rows,
                      "day_pl": day_pl, "day_ret_pct": round(day_pl / prev * 100, 3) if prev else 0, "capital": cur})
    return {"start": CAP0, "end": cur, "total_pct": round((cur / CAP0 - 1) * 100, 2), "curve": curve}

def main():
    syms = {}
    for p in glob.glob(os.path.join(V.CACHE, "*.json")):
        try: syms[os.path.basename(p)[:-5]] = load_vol(p)
        except Exception: pass
    all_days = sorted({d for (_, _, _, bd, _) in syms.values() for d in bd})
    npres = {d: sum(1 for (_, _, _, bd, _) in syms.values() if d in bd) for d in all_days}
    days = [d for d in all_days if npres[d] >= 150][-N_DAYS:]
    days_out = []
    for day in days:
        improved, improved_rvol = [], []
        for s, (bars, s20, s200, byday, tod) in syms.items():
            if day not in byday: continue
            dates = sorted(byday); pos = dates.index(day)
            if pos == 0: continue
            idxs = byday[day]
            if len(idxs) < 6: continue
            pclose = bars[byday[dates[pos - 1]][-1]]["close"]; o = bars[idxs[0]]["open"]
            gap = (o - pclose) / pclose * 100
            if not (V.GAP_MIN <= gap <= V.GAP_MAX) or o > MAX_PRICE: continue
            r = V.sim_one(bars, s20, s200, idxs, day, s, "sweet")
            if not r: continue
            r["premarket_gap_pct"] = round(gap, 2); r["prev_close"] = round(pclose, 2); r["today_open"] = round(o, 2)
            # RVOL at arm bar
            ahist = [v for (dd, v) in tod[r["arm_t"]] if dd < day][-KDAYS:]
            arm_vol = next((bars[j]["vol"] for j in idxs if bars[j]["dt"].strftime("%H:%M") == r["arm_t"]), 0)
            rvol = (arm_vol / (sum(ahist) / len(ahist))) if len(ahist) >= 3 and sum(ahist) > 0 else None
            r["rvol"] = round(rvol, 2) if rvol else None
            improved.append(r)
            if rvol is not None and rvol >= RVOL_MIN: improved_rvol.append(r)
        days_out.append({"day": day.isoformat(), "source": "IBKR 2-min (broad 231-name cache)",
                         "improved": V.panel(improved), "improved_rvol": V.panel(improved_rvol)})
    days_out.sort(key=lambda d: d["day"], reverse=True)
    setups = [{"key": "improved", "label": "★ IMPROVED — gap 2-4 · 3R · 45-min", "klass": "sweet"},
              {"key": "improved_rvol", "label": "🔊 IMPROVED + RVOL≥1.0", "klass": "live"}]
    data = {"generated_at": datetime.now(V.ET).isoformat(),
            "title": "Opening Power — Improved Config (gap 2-4 · 3R · 45-min) ± RVOL",
            "subtitle": "Improved sweet-spot vs the same + a relative-volume filter (arm-bar vol vs same-minute 14-day avg). RVOL helps the flat stretch slightly but costs more in trends — net worse.",
            "timeframe_min": 2, "setups": setups, "capital": CAP0, "slots": SLOTS,
            "days": days_out, "compound": {s["key"]: compound(days_out, s["key"]) for s in setups}}
    out = os.path.join(HERE_LOGS, "opening_sim_improved.json")
    json.dump(data, open(out, "w"), indent=2, default=str)
    c = data["compound"]
    print(" | ".join(f"{k}: ${c[k]['end']:.0f} ({c[k]['total_pct']:+.1f}%)" for k in c))

HERE_LOGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
if __name__ == "__main__":
    main()
