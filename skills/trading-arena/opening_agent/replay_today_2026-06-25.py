#!/usr/bin/env python3
"""Counterfactual replay of TODAY 2026-06-25's opening session — TV data source.

The live engine traded NOTHING today (data feed timed out: '0/91 ready', broker
link down). This reconstructs what WOULD have happened on today's real pre-market
funnel (the 91 gap-qualified names in opening_scan_latest.json) using today's
actual 2-min RTH bars pulled from the LIVE TradingView feed (CDP :9225) — the
same data source the live engine trades on.

Primary scenario (the GATE_AB_SWEEP_60D.md "lead", source of the +14.1%/60d):
  B GATE-OFF+GAP : coil gate OFF, top-5 by pre-market gap, enter on the 9:30 breakout.

Sizing: equal $200 dollar slots ($1000/5), full slot (G9 add assumed filled, an
upper bound), 3% risk cap, $0.05 min range, +30-min cutoff. Questrade = commission
-free so gross == net (spread/slippage NOT modeled). ONE DAY only — not the 14.1%.

NOTE: Scenario A (as-built, coil gate ON) needs SMA200 = 200 prior 2-min RTH bars.
The TV default load (~300 bars) only reaches ~half of the prior session, so A is
NOT computable on this feed — it needs the IBKR historical pull (run when the
gateway is re-authed). A is therefore reported as 'pending', not faked.
"""
import json, os, sys
from datetime import datetime, date, time as dtime
from zoneinfo import ZoneInfo

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))                 # skills/trading-arena


def _load_env():
    for line in open("/home/tonygale/openclaw/.env"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            if k and v:
                os.environ.setdefault(k, v)


_load_env()
os.environ.setdefault("OPENING_TV_CDP_PORT", "9225")
from opening_agent import classifier as C
from opening_agent import tv_bars

ET = ZoneInfo("America/New_York")
TODAY = date(2026, 6, 25)
OPEN_T = dtime(9, 30)
CFG = C.DEFAULTS
OFFSET = CFG["trade_offset"]
CUTOFF_MIN = int(os.environ.get("OPENING_SESSION_CUTOFF_MIN", "30"))
MAX_RISK = float(os.environ.get("OPENING_MAX_RISK_PCT", "3.0"))
MIN_RANGE = float(os.environ.get("OPENING_MIN_BAR_RANGE", "0.05"))
BUDGET, MAXN = 1000.0, 5
SLOT = BUDGET / MAXN
SCRATCH = "/tmp/claude-1000/-home-tonygale-openclaw/db8b5a93-7f6a-4ab2-b328-55afc31442d5/scratchpad"
CACHE = os.path.join(SCRATCH, "today_bars_tv_2026-06-25.json")
SCAN = os.path.join(os.path.dirname(_HERE), "logs", "opening_scan_latest.json")


def do_fetch(universe):
    if os.path.exists(CACHE):
        c = json.load(open(CACHE))
        if all(s in c for s in universe):
            print(f"[fetch] all {len(universe)} cached", file=sys.stderr)
            return c
    os.environ.setdefault("OPENING_CDP_PARALLEL_TABS", "4")
    print(f"[fetch] TV CDP pull of {len(universe)} symbols...", file=sys.stderr)
    raw = tv_bars.fetch_bars(universe, min_bars=200, res="2", timeout=900)
    cache = {}
    for s in universe:
        rows = []
        for b in raw.get(s) or []:
            et = datetime.fromtimestamp(b["date"], ET)
            if OPEN_T <= et.time() < dtime(16, 0):
                rows.append({"et": et.isoformat(), "ts": float(b["date"]),
                             "open": b["open"], "high": b["high"], "low": b["low"],
                             "close": b["close"], "volume": b.get("volume", 0) or 0})
        rows.sort(key=lambda r: r["ts"])
        cache[s] = rows
    json.dump(cache, open(CACHE, "w"))
    return cache


def _et(s):
    return datetime.fromisoformat(s)


def open_idx(rows):
    return next((i for i, b in enumerate(rows)
                 if _et(b["et"]).date() == TODAY and _et(b["et"]).time() >= OPEN_T), None)


def sim_long_from(bar, session):
    entry = bar["high"] + OFFSET; stop = bar["low"] - OFFSET
    cut = bar["ts"] + CUTOFF_MIN * 60
    win = [b for b in session if b["ts"] <= cut + 1]
    ent = ex = None
    for b in win:
        if b["ts"] <= bar["ts"]:
            continue
        if ent is None:
            if b["high"] >= entry:
                ent = entry
            continue
        if b["low"] <= stop:
            ex = stop; break
    if ent is None:
        return {"filled": False, "entry": entry, "stop": stop}
    if ex is None:
        ex = win[-1]["close"] if win else ent
    return {"filled": True, "entry": entry, "stop": stop, "exit": round(ex, 4),
            "pct": (ex - ent) / ent * 100, "stopped": abs(ex - stop) < 1e-9}


def main():
    scan = json.load(open(SCAN))["ranked"]
    gap = {r["symbol"]: float(r.get("pct_change") or 0) for r in scan}
    syms = [r["symbol"] for r in scan]
    bars = do_fetch(syms)

    info = {s: (bars.get(s) or [], open_idx(bars.get(s) or [])) for s in syms}
    valid = [s for s in syms if info[s][1] is not None and len(info[s][0]) - info[s][1] >= 2]
    no_data = [s for s in syms if s not in valid]

    # ---------- B: gate-off + gap-rank + 9:30 breakout (top 5 by gap) ----------
    ranked = sorted(valid, key=lambda s: gap.get(s, 0), reverse=True)
    b_take = []
    for s in ranked:
        if len(b_take) >= MAXN:
            break
        rows, oi = info[s]
        b1 = rows[oi]; entry = b1["high"] + OFFSET; stop = b1["low"] - OFFSET
        risk = (entry - stop) / entry * 100
        if risk > MAX_RISK or (entry - stop) < MIN_RANGE:
            b_take.append({"sym": s, "gap": gap.get(s, 0), "risk": risk, "sim": None, "skip": "risk-cap"})
            continue
        sim = sim_long_from(b1, rows[oi:oi + 30])
        b_take.append({"sym": s, "gap": gap.get(s, 0), "risk": risk, "sim": sim, "px": b1["open"]})
    taken = [d for d in b_take if d.get("sim")]
    b_total = sum(SLOT * d["sim"]["pct"] / 100 for d in taken if d["sim"]["filled"])

    # ---------- take-it-anyway across ALL valid names ----------
    ta = 0.0; tw = tl = tf = tn = 0
    for s in valid:
        rows, oi = info[s]
        sm = sim_long_from(rows[oi], rows[oi:oi + 30])
        if not sm["filled"]:
            tn += 1; continue
        ta += SLOT * sm["pct"] / 100
        if sm["pct"] > 1e-6: tw += 1
        elif sm["pct"] < -1e-6: tl += 1
        else: tf += 1

    out = []; P = out.append
    P(f"\n========== TODAY {TODAY} OPENING REPLAY (TV live feed) ==========")
    P(f"pre-market gap-qualified universe: {len(syms)} | usable today RTH bars: {len(valid)} | no-data: {len(no_data)}")
    P(f"config: risk cap {MAX_RISK}% | min range ${MIN_RANGE} | slot ${SLOT:.0f} (full) | cutoff +{CUTOFF_MIN}m (10:00 ET)")
    if no_data:
        P(f"no-data (TV returned nothing today): {' '.join(no_data)}")

    P(f"\n--- B: GATE-OFF + GAP-RANK + 9:30 BREAKOUT  (top {MAXN} by pre-market gap) ---")
    P(f"{'sym':6}{'gap%':>8}{'9:30 open':>11}{'risk%':>7}{'fill':>6}{'exit pct':>10}{'$ on $200':>11}")
    for d in b_take:
        if not d.get("sim"):
            P(f"{d['sym']:6}{d['gap']:>+8.1f}{'-':>11}{d['risk']:>6.1f}  SKIP {d['skip']}")
            continue
        sm = d["sim"]; f = "yes" if sm["filled"] else "no"
        pct = f"{sm['pct']:+.2f}%" if sm["filled"] else "-"
        dol = SLOT * sm["pct"] / 100 if sm["filled"] else 0.0
        P(f"{d['sym']:6}{d['gap']:>+8.1f}{d['px']:>11.2f}{d['risk']:>6.1f}{f:>6}{pct:>10}{dol:>+11.2f}")
    P(f"  TOTAL P&L on $1000: ${b_total:+.2f} ({b_total/BUDGET*100:+.2f}%)  [ONE day]")

    P(f"\n--- TAKE-IT-ANYWAY across all {len(valid)} valid names (9:30 breakout, gate ignored, $200 each) ---")
    P(f"  win {tw} / lose {tl} / flat {tf} / never-triggered {tn} | avg ${ta/max(1,len(valid)):+.2f}/name | sum-of-all ${ta:+.2f}")

    P(f"\n--- A: AS-BUILT (coil gate ON) --- PENDING")
    P(f"  Not computable on the TV feed (needs 200 prior 2-min RTH bars for SMA200; TV default load ~half a")
    P(f"  prior session). Run via IBKR historical once the gateway is re-authed for an apples-to-apples A/B.")

    P(f"\n(ONE trading day. Gross=net on Questrade; spread/slippage NOT modeled. Full-slot = upper-bound sizing.)")
    txt = "\n".join(out)
    print(txt)
    open(os.path.join(_HERE, "REPLAY_TODAY_2026-06-25.txt"), "w").write(txt + "\n")


if __name__ == "__main__":
    main()
