#!/usr/bin/env python3
"""One-off: sweep the pre-market gap band against a cache, loading each symbol's
series ONCE then re-simulating per band. Isolates whether the live 1-6% band is
leaving edge on the table or correctly excluding WIDE runaways.

Run with OPENING_BT_CACHE_DIR / OPENING_BT_CACHE_ANY set before invocation."""
import os, sys
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import backtest_full as bt  # noqa: E402

ET = bt.ET
END = datetime(2026, 6, 24, 20, 0, tzinfo=ET)
START = END - timedelta(days=95)

BANDS = [
    ("no filter", (0.0, 100.0)),
    ("1-4%",      (1.0, 4.0)),
    ("1-6% LIVE", (1.0, 6.0)),
    ("1-8%",      (1.0, 8.0)),
    ("1-10%",     (1.0, 10.0)),
    ("1-100% (no cap)", (1.0, 100.0)),
    ("2-6%",      (2.0, 6.0)),
    ("3-6%",      (3.0, 6.0)),
    ("0-6%",      (0.0, 6.0)),
]

def main():
    universe = bt.build_universe()
    qe = bt._executor()
    sid = bt._load_sid_cache()
    # Load every series once; keep the per-(sym,day) sessions we can re-score.
    series_by_sym = {}
    loaded = 0
    for n, sym in enumerate(universe, 1):
        try:
            s = bt.fetch_series(sym, START, END, qe, sid, cache_only=True)
        except Exception:
            continue
        if len(s) < 201:
            continue
        series_by_sym[sym] = s
        loaded += 1
        if n % 50 == 0:
            print(f"  loaded {n}/{len(universe)} ({loaded} usable)", file=sys.stderr)
    print(f"[sweep] {loaded} usable symbols from {len(universe)}", file=sys.stderr)

    # Pre-compute the trading days present per symbol.
    days_by_sym = {sym: sorted({b["et"].date() for b in s if b["et"].time() >= bt.OPEN_T})
                   for sym, s in series_by_sym.items()}

    print(f"\n{'band':18} {'trades':>7} | {'naive win%':>10} {'naive net$':>11} {'naive avg%':>11} "
          f"| {'flat win%':>10} {'flat net$':>11} {'flat avg%':>11}")
    print("-" * 110)
    rows = []
    for label, band in BANDS:
        matched = []
        for sym, s in series_by_sym.items():
            for d in days_by_sym[sym]:
                row = bt.simulate_session(sym, s, d, pm_gap=band, build_cand=False)
                if row and row.get("match"):
                    matched.append(row)
        nv = bt._scorecard(matched, "naive")
        fl = bt._scorecard(matched, "flatten")
        rows.append((label, len(matched), nv, fl))
        print(f"{label:18} {len(matched):>7} | {nv['win_rate']:>10} {nv['net_pnl']:>+11.2f} "
              f"{nv['avg_pct']:>+11.3f} | {fl['win_rate']:>10} {fl['net_pnl']:>+11.2f} {fl['avg_pct']:>+11.3f}")
    print("\n(naive = open-to-cutoff hold; flatten = the as-built EOD-flatten exit. avg% = edge per trade.)")

if __name__ == "__main__":
    main()
