#!/usr/bin/env python3
"""60-day A/B: BASELINE (live engine rules) vs VARIANT (Tony's changes), both with
the 20-minute sell-off. Data = the IBKR 2-min cache (logs/backtest_cache_ibkr/*).

CAVEAT (loud): that cache is a FIXED 27 mega-cap tech universe, NOT the real
low-priced pre-market gap funnel the strategy actually trades. Mega-caps rarely
gap 1-6% and are expensive, so this is a weak proxy — directional only, NOT the
definitive validation. Per the replay contract: load .env first, rolling-arm
9:30->9:42, entry=signal-bar high+0.01, exits as each rule set dictates.

BASELINE: TIGHT on, location by OPEN, one-bar stop, engine breakeven+push trail.
VARIANT : TIGHT off, location by CLOSE (>SMA200), stop capped 1.5%, 2R target,
          breakeven at 1R, no per-bar trail. Both flatten at the 20-min sell-off.
Metric: per-trade return % (price-normalized; avoids mega-cap $ distortion).
"""
import os, sys, glob, json, importlib.util
from datetime import datetime, time as dtime, timedelta
from collections import defaultdict
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
def _load_env():
    for line in open("/home/tonygale/openclaw/.env"):
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            if k and v: os.environ.setdefault(k, v)
_load_env()
spec = importlib.util.spec_from_file_location("simmod", os.path.join(HERE, "sim_opening_2026-06-26.py"))
sm = importlib.util.module_from_spec(spec); spec.loader.exec_module(sm)
C, E, IND, SimEngine = sm.C, sm.E, sm.IND, sm.SimEngine

ET = ZoneInfo("America/New_York")
OPEN_T = dtime(9, 30); ARM_END = dtime(9, 44)
SELLOFF_MIN = 20; STOP_CAP = 0.015; RR = 2.0; GAP_MIN, GAP_MAX = 1.0, 6.0
CACHE = os.path.join(HERE, "..", "logs", "backtest_cache_ibkr_broad")
NDAYS = 60

def load_symbol(path):
    d = json.load(open(path))
    bars = []
    for b in d.get("bars", []):
        dt = datetime.fromisoformat(b["et"])
        if OPEN_T <= dt.time() <= dtime(16, 0):     # RTH only
            bars.append({"dt": dt, "open": b["open"], "high": b["high"],
                         "low": b["low"], "close": b["close"]})
    bars.sort(key=lambda x: x["dt"])
    closes = [x["close"] for x in bars]
    # rolling SMAs over the full series (true SMA20/SMA200 of 2-min closes)
    sma20 = IND.sma_series(closes, 20) if hasattr(IND, "sma_series") else _roll(closes, 20)
    sma200 = _roll(closes, 200)
    by_day = defaultdict(list)
    for i, x in enumerate(bars):
        by_day[x["dt"].date()].append(i)
    return bars, sma20, sma200, by_day

def _roll(xs, n):
    out = [None] * len(xs); s = 0.0
    for i, v in enumerate(xs):
        s += v
        if i >= n: s -= xs[i - n]
        if i >= n - 1: out[i] = s / n
    return out

def run_baseline(bars, sma20, sma200, idxs):
    """rolling-arm classify (TIGHT on, loc by open); drive SimEngine (no add), 20-min cutoff."""
    arm_i = None; entry = stop = None
    for i in idxs:
        if bars[i]["dt"].time() > ARM_END: break
        if sma200[i] is None: continue
        prior = bars[max(0, i - 30):i]
        v = C.classify_opening("S", bars[i], prior, sma20[i], sma200[i])
        if v.decision == "MATCH_LONG":
            arm_i = i; entry = bars[i]["high"] + 0.01; stop = bars[i]["low"] - 0.01; break
    if arm_i is None: return None
    eng = SimEngine("S", cfg={"max_adds": 0})
    eng.side, eng.state, eng.bar1 = 1, E.ARMED, bars[arm_i]
    eng.entry_price, eng.stop_price, eng.shares = entry, stop, 100
    selloff = datetime.combine(bars[arm_i]["dt"].date(), OPEN_T, ET) + timedelta(minutes=SELLOFF_MIN)
    for i in idxs:
        if i <= arm_i: continue
        b = bars[i]
        eng.on_bar(b, complete=True)
        if eng.state == E.FLAT:
            return (eng.stop_price - entry) / entry * 100      # stopped (exit ~stop)
        if b["dt"] >= selloff and eng.state in (E.IN_HALF, E.IN_FULL):
            return (b["close"] - entry) / entry * 100          # 20-min sell-off
    return None

def run_variant(bars, sma20, sma200, idxs):
    """power+close>SMA200 (no TIGHT); capped 1.5% stop; 2R target; breakeven; 20-min."""
    arm_i = None
    for i in idxs:
        if bars[i]["dt"].time() > ARM_END: break
        if sma200[i] is None: continue
        prior = bars[max(0, i - 30):i]
        if C.bar_signal(bars[i], prior) > 0 and bars[i]["close"] > sma200[i]:
            arm_i = i; break
    if arm_i is None: return None
    entry = bars[arm_i]["high"] + 0.01
    stop = max(bars[arm_i]["low"] - 0.01, entry * (1 - STOP_CAP))
    risk = entry - stop; target = entry + RR * risk
    selloff = datetime.combine(bars[arm_i]["dt"].date(), OPEN_T, ET) + timedelta(minutes=SELLOFF_MIN)
    in_pos = False; cur = stop; be = False
    for i in idxs:
        if i <= arm_i: continue
        b = bars[i]
        if not in_pos and b["high"] >= entry: in_pos = True
        if in_pos:
            if b["high"] >= target: return (target - entry) / entry * 100
            if b["low"] <= cur: return (cur - entry) / entry * 100
            if not be and b["high"] >= entry + risk: cur = entry; be = True
            if b["dt"] >= selloff: return (b["close"] - entry) / entry * 100
    return None

def main():
    syms = {}
    for p in glob.glob(os.path.join(CACHE, "*.json")):
        s = os.path.basename(p)[:-5]
        try: syms[s] = load_symbol(p)
        except Exception as e: print(f"skip {s}: {e}", file=sys.stderr)
    all_days = sorted({d for (_, _, _, bd) in syms.values() for d in bd})
    days = all_days[-NDAYS:]
    base, var = [], []
    for s, (bars, sma20, sma200, by_day) in syms.items():
        dates = sorted(by_day)
        for di, day in enumerate(dates):
            if day not in days: continue
            idxs = by_day[day]
            if len(idxs) < 12 or di == 0: continue
            prev_close = bars[by_day[dates[di - 1]][-1]]["close"]
            o = bars[idxs[0]]["open"]
            gap = (o - prev_close) / prev_close * 100
            if not (GAP_MIN <= gap <= GAP_MAX): continue
            rb = run_baseline(bars, sma20, sma200, idxs)
            rv = run_variant(bars, sma20, sma200, idxs)
            if rb is not None: base.append(rb)
            if rv is not None: var.append(rv)
    def stats(xs):
        if not xs: return {"trades": 0}
        wins = [x for x in xs if x > 0]
        return {"trades": len(xs), "win_pct": round(len(wins) / len(xs) * 100, 1),
                "avg_ret_pct": round(sum(xs) / len(xs), 3),
                "total_ret_pct": round(sum(xs), 2),
                "best": round(max(xs), 2), "worst": round(min(xs), 2)}
    out = {"window_days": len(days), "from": str(days[0]), "through": str(days[-1]),
           "universe": f"{len(syms)} IBKR mega-cap tech (proxy, NOT the real funnel)",
           "gap_band": [GAP_MIN, GAP_MAX], "selloff_min": SELLOFF_MIN,
           "baseline": stats(base), "variant": stats(var)}
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
