#!/usr/bin/env python3
"""Does taking MORE trades help? Same criteria as baseline + improved (sim), but compare:
  A) top-5  @ $200/slot   (the current sizing — $1000 / 5)
  B) top-10 @ $100/slot   ($1000 / 10, take the first 10 to arm)
Per-trade %-return is slot-agnostic, so the only differences are (1) how many names you
take each day and (2) affordability — a $100 slot can't buy a name priced over $100, so
those are excluded (modelled). Recent ~23-session window, baseline + improved."""
import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "opening_agent"))
import sim_variant_ibkr_days as V


def load_syms():
    syms = {}
    for p in glob.glob(os.path.join(V.CACHE, "*.json")):
        try:
            syms[os.path.basename(p)[:-5]] = V.load(p)
        except Exception:
            pass
    return syms


def day_picks(syms, day, cfg):
    out = []
    for s, (bars, s20, s200, byday) in syms.items():
        if day not in byday:
            continue
        dts = sorted(byday); pos = dts.index(day)
        if pos == 0 or len(byday[day]) < 12:
            continue
        idxs = byday[day]; pclose = bars[byday[dts[pos - 1]][-1]]["close"]; o = bars[idxs[0]]["open"]
        gap = (o - pclose) / pclose * 100
        if not (V.GAP_MIN <= gap <= V.GAP_MAX) or o > V.MAX_PRICE or o < V.MIN_PRICE:
            continue
        r = V.sim_one(bars, s20, s200, idxs, day, s, cfg)
        if r and r.get("position_cost"):
            out.append({"sym": s, "entry": r["entry"], "arm_t": r["arm_t"],
                        "ret": r["realized_pl"] / r["position_cost"] * 100})
    out.sort(key=lambda x: (x["arm_t"] or "99:99"))
    return out


def compound(daylists, slots, slot_usd, afford=True):
    cap = 1000.0; rets = []; daytr = []
    for picks in daylists:
        elig = [p for p in picks if (not afford or p["entry"] <= slot_usd)]
        tr = elig[:slots]; slot = cap / slots
        for p in tr:
            cap += slot * p["ret"] / 100; rets.append(p["ret"])
        daytr.append(len(tr))
    return dict(end=round(cap, 2), pct=round((cap / 1000 - 1) * 100, 2), trades=len(rets),
                avg=round(sum(rets) / len(rets), 3) if rets else 0,
                win=round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1) if rets else 0,
                avg_per_day=round(sum(daytr) / len(daytr), 1) if daytr else 0)


def main():
    syms = load_syms()
    adays = sorted({d for (_, _, _, bd) in syms.values() for d in bd})
    recent = [d for d in adays if d.isoformat() >= "2026-05-22"]
    print(f"TOP-5 @ $200  vs  TOP-10 @ $100   (recent {len(recent)} sessions, $1000 budget)")
    for cfg, label in (("baseline", "BASELINE"), ("sweet", "IMPROVED (sim)")):
        dl = [day_picks(syms, d, cfg) for d in recent]
        a = compound(dl, 5, 200); b = compound(dl, 10, 100)
        print(f"\n=== {label} ===")
        print(f"  top-5  @ $200: ${a['end']:>7} ({a['pct']:+6.2f}%) | {a['trades']:>3} trades, "
              f"{a['avg_per_day']}/day, win {a['win']}%, avg/trade {a['avg']:+.3f}%")
        print(f"  top-10 @ $100: ${b['end']:>7} ({b['pct']:+6.2f}%) | {b['trades']:>3} trades, "
              f"{b['avg_per_day']}/day, win {b['win']}%, avg/trade {b['avg']:+.3f}%")
        d = b['pct'] - a['pct']
        print(f"  => more trades {'HELPS' if d > 0 else 'HURTS'} by {d:+.2f} pts")


if __name__ == "__main__":
    main()
