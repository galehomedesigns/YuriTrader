#!/usr/bin/env python3
"""SHORT-only edge vs execution cost, on liquid names — the make-or-break test.
FAST: prefix-sum SMAs (O(n)) instead of per-session re-slicing.

Scores short setups GROSS (zero slippage) on liquidity-filtered subsets, then sweeps
a per-side cost to find the BREAK-EVEN execution quality. Liquidity = median per-bar
dollar-volume. Reads ONLY the IBKR cache.
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

ET = ZoneInfo("America/New_York")
LOGS = os.path.join(os.path.dirname(_HERE), "logs")
CACHE = os.path.join(LOGS, "backtest_cache_ibkr_tech")
OPEN_T = time(9, 30)
CUTOFF_MIN = int(os.environ.get("OPENING_SESSION_CUTOFF_MIN", "30"))
WARMUP = 200


def load(sym):
    raw = json.load(open(os.path.join(CACHE, sym + ".json")))
    bars = [{"et": datetime.fromisoformat(b["et"]), "open": b["open"], "high": b["high"],
             "low": b["low"], "close": b["close"], "vol": b.get("volume", 0) or 0} for b in raw.get("bars", [])]
    bars.sort(key=lambda x: x["et"])
    return bars


def short_gross(bar1, session, cutoff_ts):
    entry_lvl = C.entry_level_short(bar1)
    stop_lvl = C.stop_level_short(bar1)
    window = [b for b in session if b["et"].timestamp() <= cutoff_ts + 1]
    entered = exit_px = None
    for b in window[1:]:
        if entered is None:
            if b["low"] <= entry_lvl:
                entered = entry_lvl
            continue
        if b["high"] >= stop_lvl:
            exit_px = stop_lvl
            break
    if entered is None:
        return None
    if exit_px is None:
        exit_px = window[-1]["close"]
    return (entered - exit_px) / entered * 100


def run():
    files = [f[:-5] for f in os.listdir(CACHE) if f.endswith(".json") and not f.startswith("_")]
    liq, trades = {}, []
    for sym in files:
        try:
            bars = load(sym)
        except Exception:                              # noqa: BLE001
            continue
        dv = [b["close"] * b["vol"] for b in bars if b["vol"]]
        liq[sym] = st.median(dv) if dv else 0.0
        closes = [b["close"] for b in bars]
        # prefix sums for O(1) SMA: pre[k] = sum(closes[:k])
        pre = [0.0] * (len(closes) + 1)
        for i, c in enumerate(closes):
            pre[i + 1] = pre[i] + c
        sma = lambda i, n: (pre[i + 1] - pre[i + 1 - n]) / n if i + 1 >= n else None
        byday = defaultdict(list)
        for i, b in enumerate(bars):
            byday[b["et"].date()].append(i)
        for d in sorted(byday):
            oi = next((i for i in byday[d] if bars[i]["et"].time() >= OPEN_T), None)
            if oi is None or oi < WARMUP:
                continue
            # Only the recent bars matter: classifier looks back ~20, sim needs the
            # ~15-bar cutoff window. Avoid copying the whole 90k-bar prefix per session.
            bar1, prior = bars[oi], bars[max(0, oi - 60):oi]
            smf, sms = sma(oi, 20), sma(oi, 200)
            if C.classify_opening(sym, bar1, prior, smf, sms).decision != "MATCH_SHORT":
                continue
            cutoff_ts = datetime.combine(d, OPEN_T, ET).timestamp() + CUTOFF_MIN * 60
            r = short_gross(bar1, bars[oi:oi + 25], cutoff_ts)
            if r is not None:
                trades.append((str(d), sym, r))
    return liq, trades


def stats(sel):
    if not sel:
        return None
    days = sorted({r[0] for r in sel})
    cut = days[len(days) // 2]
    isr = [r[2] for r in sel if r[0] < cut]
    oos = [r[2] for r in sel if r[0] >= cut]
    bysym = defaultdict(list)
    for r in sel:
        bysym[r[1]].append(r[2])
    pos = sum(1 for v in bysym.values() if sum(v) > 0)
    m = lambda g: st.mean(g) if g else 0.0
    return {"n": len(sel), "gross": m([r[2] for r in sel]), "is": m(isr), "oos": m(oos),
            "sym": f"{pos}/{len(bysym)}"}


def main():
    liq, trades = run()
    ranked = sorted(liq, key=lambda s: liq[s], reverse=True)
    tiers = {"ALL (72)": set(liq), "top-40 liq": set(ranked[:40]), "top-20 liq": set(ranked[:20])}
    print(f"[short-exec] {len(trades)} short trades total", file=sys.stderr)
    print(f"\n{'universe':<14}{'n':>6}{'gross%':>9}{'IS%':>8}{'OOS%':>8}{'sym+':>9}{'breakeven':>12}")
    print("-" * 70)
    best = None
    for lab, syms in tiers.items():
        s = stats([t for t in trades if t[1] in syms])
        if not s:
            continue
        be = s["gross"] / 2 * 100
        print(f"{lab:<14}{s['n']:>6}{s['gross']:>9.3f}{s['is']:>8.3f}{s['oos']:>8.3f}{s['sym']:>9}{be:>9.1f}bps")
        if lab == "top-20 liq":
            best = s
    if best:
        print(f"\nNET avg%/trade on top-20 liquid (gross {best['gross']:+.3f}%) vs per-side cost:")
        for c in (0.0, 0.01, 0.02, 0.03, 0.05, 0.08):
            print(f"   {c:.2f}%/side -> net {best['gross'] - 2*c:+.3f}%")
        print("(realistic liquid-name marketable-limit ~0.02-0.03%/side incl. fees)")


if __name__ == "__main__":
    main()
