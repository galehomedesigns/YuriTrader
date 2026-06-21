#!/usr/bin/env python3
"""Phase 3 — stacked-filter combinations on the gap-qualified opening setups.
IS = first half of sessions, OOS = second half. Writes a summary for the dashboard."""
import json, os, sys, statistics as st
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from opening_agent.indicator_edge import build_rows, _split, LOGS

rows = build_rows()
is_r, oos_r, cut = _split(rows)


def ev(rs):
    r = [x["ret"] for x in rs]
    n = len(r)
    return {"n": n, "mean": round(st.mean(r), 3) if r else 0.0,
            "win": round(100 * sum(1 for x in r if x > 0) / n, 1) if n else 0.0}


def rv(r):
    return r.get("rvol") or 0


def atr(r):
    return r.get("atr_pct") or 0


COMBOS = {
    "baseline (all gap-qualified)": lambda r: True,
    "rvol >= 1.5": lambda r: rv(r) >= 1.5,
    "gap >= 5%": lambda r: r["gap"] >= 5,
    "gap >= 8%": lambda r: r["gap"] >= 8,
    "ATR% in 1-3": lambda r: 1 <= atr(r) < 3,
    "bull_elephant tag": lambda r: "bull_elephant" in (r.get("tags") or []),
    "gap>=5 & ATR%1-3": lambda r: r["gap"] >= 5 and 1 <= atr(r) < 3,
    "gap>=5 & rvol>=1.5": lambda r: r["gap"] >= 5 and rv(r) >= 1.5,
    "gap>=5 & ATR%1-3 & rvol>=1.5": lambda r: r["gap"] >= 5 and 1 <= atr(r) < 3 and rv(r) >= 1.5,
    "gap>=3 & ATR%>=1 & bull_eleph": lambda r: r["gap"] >= 3 and atr(r) >= 1 and "bull_elephant" in (r.get("tags") or []),
}


def filt(rs, fn):
    return [r for r in rs if fn(r)]


out = []
print(f"{'FILTER':<34}{'IS  n / mean% / win':>24}{'OOS  n / mean% / win':>24}")
for name, fn in COMBOS.items():
    i, o = ev(filt(is_r, fn)), ev(filt(oos_r, fn))
    out.append({"filter": name, "is": i, "oos": o})
    print(f"{name:<34}{i['n']:>7} /{i['mean']:>+7.3f} /{i['win']:>5.0f}"
          f"{o['n']:>9} /{o['mean']:>+7.3f} /{o['win']:>5.0f}")

json.dump({"split_at": cut, "combos": out}, open(os.path.join(LOGS, "combo_edge_summary.json"), "w"),
          indent=2, default=str)
print(f"\nwrote {os.path.join(LOGS, 'combo_edge_summary.json')}")
