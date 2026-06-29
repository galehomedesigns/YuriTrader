#!/usr/bin/env python3
"""Robustness sweep — price range x gap range x coil level for the opening setup.

Answers "which PRICE RANGE / GAP RANGE / COIL LEVEL is best for Power Open" — but
honestly: NOT by picking the highest-return cell (that's the overfitting trap),
but by showing each bucket's IN-SAMPLE vs OUT-OF-SAMPLE result AND per-symbol
breadth, so a sample fluke is visible. A bucket only "wins" if it's positive in
BOTH halves and broad across symbols.

One pass over the combined IBKR cache (mega + low-priced), one row per gap-qualified
(symbol, session): records entry price, gap%, coil_atr (=|SMA20-SMA200|/ATR on the
2-min series, the live TIGHT metric), and the forward 20-min return entry->cutoff
or bar1-low stop, with REALISTIC price-scaled slippage (cents/share floor — so cheap
stocks pay the spread they really would). Then buckets across all three dimensions.

Reads ONLY the IBKR caches. No network, no live system.
"""
import json
import os
import sys
import statistics as st
from collections import defaultdict
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
_HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(os.path.dirname(_HERE), "logs")
_DEFAULT_CACHES = [os.path.join(LOGS, "backtest_cache_ibkr"), os.path.join(LOGS, "backtest_cache_ibkr_low")]
CACHES = ([os.path.join(LOGS, d) for d in os.environ["OPENING_SWEEP_CACHE_DIRS"].split(",")]
          if os.environ.get("OPENING_SWEEP_CACHE_DIRS") else _DEFAULT_CACHES)
OPEN_T = time(9, 30)
CUTOFF_MIN = int(os.environ.get("OPENING_SESSION_CUTOFF_MIN", "30"))
WARMUP = 210
GAP_MIN, GAP_MAX = 1.0, 25.0
SLIP_PCT = 0.0010
SLIP_CENTS = float(os.environ.get("OPENING_BT_SLIP_CENTS", "0.02"))


def _slip(px):
    return max(SLIP_PCT, SLIP_CENTS / px if px > 0 else 0.0)


def load_all():
    series = {}
    for d in CACHES:
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if not fn.endswith(".json") or fn.startswith("_"):
                continue
            try:
                raw = json.load(open(os.path.join(d, fn)))
                bars = [{"et": datetime.fromisoformat(b["et"]), "o": b["open"], "h": b["high"],
                         "l": b["low"], "c": b["close"], "v": b["volume"]} for b in raw.get("bars", [])]
                bars.sort(key=lambda x: x["et"])
                if len(bars) >= WARMUP + 5:
                    series.setdefault(fn[:-5], bars)
            except Exception:                               # noqa: BLE001
                continue
    return series


def _sma(vals, n, i):
    return sum(vals[i - n + 1:i + 1]) / n if i + 1 >= n else None


def _atr(bars, i, n=14):
    if i < n:
        return None
    trs = []
    for j in range(i - n + 1, i + 1):
        pc = bars[j - 1]["c"]
        trs.append(max(bars[j]["h"] - bars[j]["l"], abs(bars[j]["h"] - pc), abs(bars[j]["l"] - pc)))
    return sum(trs) / len(trs) if trs else None


def build_rows(series):
    """One row per gap-qualified opening setup: {date,symbol,price,gap,coil,ret}."""
    rows = []
    for sym, bars in series.items():
        closes = [b["c"] for b in bars]
        byday = defaultdict(list)
        for i, b in enumerate(bars):
            byday[b["et"].date()].append(i)
        prev_close = None
        for d in sorted(byday):
            idxs = byday[d]
            oi = next((i for i in idxs if bars[i]["et"].time() >= OPEN_T), None)
            day_last = bars[idxs[-1]]["c"]
            if oi is None or prev_close is None or oi < WARMUP:
                prev_close = day_last
                continue
            gap = (bars[oi]["o"] - prev_close) / prev_close * 100
            prev_close = day_last
            if not (GAP_MIN <= gap <= GAP_MAX):
                continue
            bar1 = bars[oi]
            s20, s200, atr = _sma(closes, 20, oi), _sma(closes, 200, oi), _atr(bars, oi)
            coil = abs(s20 - s200) / atr if (s20 and s200 and atr) else None
            entry = bar1["c"] * (1 + _slip(bar1["c"]))
            stop = bar1["l"]
            cutoff_ts = datetime.combine(d, OPEN_T, ET).timestamp() + CUTOFF_MIN * 60
            exit_px, j = None, oi + 1
            last = bar1
            while j < len(bars) and bars[j]["et"].timestamp() <= cutoff_ts + 1:
                last = bars[j]
                if stop < bar1["c"] and bars[j]["l"] <= stop:
                    exit_px = stop * (1 - _slip(stop))
                    break
                j += 1
            if exit_px is None:
                exit_px = last["c"] * (1 - _slip(last["c"]))
            ret = (exit_px - entry) / entry * 100
            rows.append({"date": str(d), "symbol": sym, "price": round(bar1["c"], 2),
                         "gap": round(gap, 2), "coil": round(coil, 3) if coil else None,
                         "ret": round(ret, 3)})
    return rows


def _summ(rows):
    if not rows:
        return None
    days = sorted({r["date"] for r in rows})
    cut = days[len(days) // 2]
    isr = [r["ret"] for r in rows if r["date"] < cut]
    oos = [r["ret"] for r in rows if r["date"] >= cut]
    bysym = defaultdict(list)
    for r in rows:
        bysym[r["symbol"]].append(r["ret"])
    pos_syms = sum(1 for v in bysym.values() if sum(v) > 0)
    def m(g):
        return st.mean(g) if g else 0.0
    return {"n": len(rows), "avg": round(m([r["ret"] for r in rows]), 3),
            "is": round(m(isr), 3), "oos": round(m(oos), 3),
            "sym_pos": pos_syms, "sym_tot": len(bysym),
            "robust": (m(isr) > 0 and m(oos) > 0 and len(rows) >= 50
                       and pos_syms >= 0.55 * len(bysym))}


def sweep(rows, name, key, buckets):
    print(f"\n=== {name} ===")
    print(f"{'bucket':<12}{'n':>6}{'avg%':>8}{'IS%':>8}{'OOS%':>8}{'sym+':>9}{'robust':>8}")
    for lab, lo, hi in buckets:
        sel = [r for r in rows if r[key] is not None and lo <= r[key] < hi]
        s = _summ(sel)
        if not s:
            print(f"{lab:<12}{0:>6}")
            continue
        syms = f"{s['sym_pos']}/{s['sym_tot']}"
        print(f"{lab:<12}{s['n']:>6}{s['avg']:>8.3f}{s['is']:>8.3f}{s['oos']:>8.3f}"
              f"{syms:>9}{('YES' if s['robust'] else '-'):>8}")


def main():
    series = load_all()
    print(f"[sweep] {len(series)} symbols across caches | slippage = max({SLIP_PCT*100:.2f}%, "
          f"{SLIP_CENTS*100:.0f}c/${'px'}) per side", file=sys.stderr)
    rows = build_rows(series)
    print(f"[sweep] {len(rows)} gap-qualified setups", file=sys.stderr)
    if not rows:
        return
    days = sorted({r["date"] for r in rows})
    print(f"window {days[0]} -> {days[-1]} | IS/OOS split {days[len(days)//2]}")
    sweep(rows, "PRICE RANGE", "price",
          [("<$2", 0, 2), ("$2-5", 2, 5), ("$5-20", 5, 20), ("$20-50", 20, 50),
           ("$50-150", 50, 150), ("$150+", 150, 1e9)])
    sweep(rows, "GAP RANGE", "gap",
          [("1-2%", 1, 2), ("2-3%", 2, 3), ("3-5%", 3, 5), ("5-8%", 5, 8),
           ("8-15%", 8, 15), ("15-25%", 15, 25)])
    print("\n=== COIL LEVEL (cumulative: trades with coil<=MULT) ===")
    print(f"{'coil<=':<12}{'n':>6}{'avg%':>8}{'IS%':>8}{'OOS%':>8}{'sym+':>9}{'robust':>8}")
    for mult in (0.5, 1.0, 1.5, 2.0, 3.0, 1e9):
        sel = [r for r in rows if r["coil"] is not None and r["coil"] <= mult]
        s = _summ(sel)
        if s:
            lab = "all" if mult > 1e8 else f"{mult:g}"
            syms = f"{s['sym_pos']}/{s['sym_tot']}"
            print(f"{lab:<12}{s['n']:>6}{s['avg']:>8.3f}{s['is']:>8.3f}{s['oos']:>8.3f}"
                  f"{syms:>9}{('YES' if s['robust'] else '-'):>8}")


if __name__ == "__main__":
    main()
