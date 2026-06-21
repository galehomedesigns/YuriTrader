#!/usr/bin/env python3
"""Phase 2 — indicator edge search on the gap-qualified opening setups.

Question: among stocks that ALREADY meet the pre-market gap criteria (the proven
edge), which OPENING-BAR indicator (volume / RSI / ATR / ADX / VWAP / gap size /
power-bar tag …) would have predicted a better 20-min outcome?

Uniform, bot-agnostic base population (maximizes sample, isolates the indicator):
  * one row per gap-qualified (symbol, session)
  * decision point = close of the FIRST completed 2-min bar (the 9:30-9:32 bar),
    indicators computed on the ~210 trailing bars ending there (full warmup)
  * entry = that bar's close; exit = the cutoff close, OR the bar-1-low protective
    stop if it's hit first (the standard opening exit); return in %
Then for each candidate indicator we bucket the rows and measure mean forward
return per bucket — with an IN-SAMPLE (first half of sessions) vs OUT-OF-SAMPLE
(second half) split, because on ~40 sessions an in-sample-only "edge" is usually
noise. An indicator only counts if its best bucket beats baseline OUT of sample too.

Honest limits: ~2 months / one regime; pre-market VOLUME + ranking still proxied;
this finds ASSOCIATIONS, not guarantees. Reads cached candles only; no network.
"""
import json
import os
import sys
import statistics as st
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from opening_agent.bot_arena_stocks import (load_series, gap_universe, _asset,  # noqa: E402
                                            OPEN_T, CUTOFF_MIN, LOGS)
from opening_agent import classifier as C                                       # noqa: E402
from datetime import datetime                                                   # noqa: E402
from zoneinfo import ZoneInfo                                                   # noqa: E402

ET = ZoneInfo("America/New_York")
SUMMARY = os.path.join(LOGS, "indicator_edge_summary.json")


def build_rows():
    """One row per gap-qualified (symbol, session): opening-bar indicators + the
    20-min forward return (cutoff or bar-1-low stop)."""
    series = load_series()
    uni = gap_universe(series)
    rows = []
    for day in sorted(uni):
        cutoff_ts = datetime.combine(day, OPEN_T, ET).timestamp() + CUTOFF_MIN * 60
        for sym, oi, gap in uni[day]:
            bars = series[sym]
            bar1 = bars[oi]
            entry = bar1["close"]
            if entry <= 0:
                continue
            stop = C.stop_level_long(bar1)
            # forward path: bars after bar1 up to the cutoff
            exit_px, reason = None, "cutoff"
            j = oi + 1
            last = bar1
            while j < len(bars) and bars[j]["et"].timestamp() <= cutoff_ts + 1:
                last = bars[j]
                if stop < entry and bars[j]["low"] <= stop:    # genuine protective stop
                    exit_px, reason = stop, "stop"
                    break
                j += 1
            if exit_px is None:
                exit_px = last["close"]
            ret = (exit_px - entry) / entry * 100
            d = _asset(sym, bars, oi, bar1["open"])            # indicators AT bar1
            tags = []
            try:
                tags = sorted(C.classify_bar(bar1, bars[:oi]))
            except Exception:                                   # noqa: BLE001
                tags = []
            price = entry
            # Coil-separation ratio = |SMA20-SMA200| / ATR — the exact metric the
            # live TIGHT gate fires on (advisory_monitor: smf,sms=sma(20),sma(200);
            # market_state TIGHT iff sep/ATR <= tight_atr_mult=1.0). Computed here so
            # we can test the gate the engine ALWAYS applies but nothing ever scored.
            coil_atr = None
            closes = getattr(d, "closes", None) or []
            atr_val = getattr(d, "atr_14", None)
            if len(closes) >= 200 and atr_val:
                smf = sum(closes[-20:]) / 20.0
                sms = sum(closes[-200:]) / 200.0
                coil_atr = abs(smf - sms) / atr_val
            rows.append({
                "date": str(day), "symbol": sym, "ret": ret, "reason": reason,
                "gap": gap, "coil_atr": coil_atr,
                "rvol": getattr(d, "rvol", None),
                "rsi": getattr(d, "rsi_14", None),
                "adx": getattr(d, "adx_14", None),
                "atr_pct": (getattr(d, "atr_14", 0) / price * 100) if (getattr(d, "atr_14", None) and price) else None,
                "macd_bull": bool(getattr(d, "macd_bullish", False)),
                "above_vwap": (price > getattr(d, "vwap_val", 0)) if getattr(d, "vwap_val", None) else None,
                "bar1_change": ((bar1["close"] - bar1["open"]) / bar1["open"] * 100) if bar1["open"] else 0.0,
                "above_ema50": (price > getattr(d, "ema_50", 0)) if getattr(d, "ema_50", None) else None,
                "tags": tags,
            })
    return rows


def _mean(xs):
    return st.mean(xs) if xs else 0.0


def _split(rows):
    """In-sample = first half of sessions, OOS = second half (time-ordered)."""
    days = sorted({r["date"] for r in rows})
    cut = days[len(days) // 2] if days else None
    is_rows = [r for r in rows if r["date"] < cut]
    oos_rows = [r for r in rows if r["date"] >= cut]
    return is_rows, oos_rows, cut


def numeric_buckets(rows, key, edges, labels):
    """Bucket by a numeric indicator; return per-bucket (label, n, mean_ret)."""
    out = []
    for lab, lo, hi in zip(labels, edges[:-1], edges[1:]):
        sel = [r["ret"] for r in rows if r.get(key) is not None and lo <= r[key] < hi]
        out.append({"bucket": lab, "n": len(sel), "mean": round(_mean(sel), 3)})
    return out


def bool_buckets(rows, key):
    out = []
    for lab, want in (("true", True), ("false", False)):
        sel = [r["ret"] for r in rows if r.get(key) is not None and bool(r[key]) is want]
        out.append({"bucket": lab, "n": len(sel), "mean": round(_mean(sel), 3)})
    return out


def tag_buckets(rows, tags):
    out = []
    for tg in tags:
        sel = [r["ret"] for r in rows if tg in (r.get("tags") or [])]
        if sel:
            out.append({"bucket": tg, "n": len(sel), "mean": round(_mean(sel), 3)})
    return out


# candidate indicators: (display, kind, key, edges, labels)
NUMERIC = [
    ("Relative volume (rvol)", "rvol", [0, 1, 1.5, 2, 3, 1e9], ["<1", "1-1.5", "1.5-2", "2-3", "3+"]),
    ("Gap size %", "gap", [1, 2, 3, 5, 8, 1e9], ["1-2", "2-3", "3-5", "5-8", "8+"]),
    # Coil sep/ATR: the live TIGHT gate fires at <=1.0. The first two buckets are
    # the TIGHT region (what the engine trades), the rest are WIDE (rejected) —
    # so comparing their forward returns IS the gate-value test.
    ("Coil sep/ATR (TIGHT gate)", "coil_atr", [0, 0.5, 1.0, 2.0, 4.0, 1e9],
     ["<0.5", "0.5-1", "1-2", "2-4", "4+"]),
    ("RSI(14)", "rsi", [0, 40, 50, 60, 70, 101], ["<40", "40-50", "50-60", "60-70", "70+"]),
    ("ATR % of price", "atr_pct", [0, 0.5, 1, 2, 4, 1e9], ["<0.5", "0.5-1", "1-2", "2-4", "4+"]),
    ("ADX(14)", "adx", [0, 15, 20, 25, 35, 1e9], ["<15", "15-20", "20-25", "25-35", "35+"]),
    ("Bar-1 candle change %", "bar1_change", [-1e9, -0.5, 0, 0.5, 1, 1e9], ["<-0.5", "-0.5-0", "0-0.5", "0.5-1", "1+"]),
]
BOOLS = [("MACD bullish", "macd_bull"), ("Above VWAP", "above_vwap"), ("Above EMA50", "above_ema50")]
TAGS = ["bull_elephant", "bear_elephant", "bottoming_tail", "topping_tail", "small"]


def analyze(rows):
    is_rows, oos_rows, cut = _split(rows)
    base_all = round(_mean([r["ret"] for r in rows]), 3)
    base_is = round(_mean([r["ret"] for r in is_rows]), 3)
    base_oos = round(_mean([r["ret"] for r in oos_rows]), 3)
    findings = []
    def best_bucket(buckets):
        cand = [b for b in buckets if b["n"] >= 15]      # ignore tiny buckets
        return max(cand, key=lambda b: b["mean"]) if cand else None
    for disp, key, edges, labels in NUMERIC:
        allb = numeric_buckets(rows, key, edges, labels)
        isb = {b["bucket"]: b for b in numeric_buckets(is_rows, key, edges, labels)}
        oosb = {b["bucket"]: b for b in numeric_buckets(oos_rows, key, edges, labels)}
        bb = best_bucket(allb)
        if bb:
            findings.append({"indicator": disp, "best_bucket": bb["bucket"], "n": bb["n"],
                             "mean_all": bb["mean"], "lift_all": round(bb["mean"] - base_all, 3),
                             "is_mean": isb.get(bb["bucket"], {}).get("mean"),
                             "oos_mean": oosb.get(bb["bucket"], {}).get("mean"),
                             "oos_lift": round((oosb.get(bb["bucket"], {}).get("mean") or 0) - base_oos, 3),
                             "buckets": allb})
    for disp, key in BOOLS:
        allb = bool_buckets(rows, key)
        isb = {b["bucket"]: b for b in bool_buckets(is_rows, key)}
        oosb = {b["bucket"]: b for b in bool_buckets(oos_rows, key)}
        bb = best_bucket(allb)
        if bb:
            findings.append({"indicator": disp, "best_bucket": bb["bucket"], "n": bb["n"],
                             "mean_all": bb["mean"], "lift_all": round(bb["mean"] - base_all, 3),
                             "is_mean": isb.get(bb["bucket"], {}).get("mean"),
                             "oos_mean": oosb.get(bb["bucket"], {}).get("mean"),
                             "oos_lift": round((oosb.get(bb["bucket"], {}).get("mean") or 0) - base_oos, 3),
                             "buckets": allb})
    tg_all = tag_buckets(rows, TAGS)
    tg_oos = {b["bucket"]: b for b in tag_buckets(oos_rows, TAGS)}
    for b in tg_all:
        if b["n"] >= 15:
            findings.append({"indicator": f"Power-bar tag: {b['bucket']}", "best_bucket": "present",
                             "n": b["n"], "mean_all": b["mean"], "lift_all": round(b["mean"] - base_all, 3),
                             "is_mean": None, "oos_mean": tg_oos.get(b["bucket"], {}).get("mean"),
                             "oos_lift": round((tg_oos.get(b["bucket"], {}).get("mean") or 0) - base_oos, 3),
                             "buckets": [b]})
    # robust ONLY if the best bucket beats baseline in BOTH halves (in-sample AND
    # out-of-sample). A positive OOS lift alone is not enough — the baseline itself
    # is regime-dependent (IS negative, OOS positive), so OOS-only "edges" usually
    # just ride the favorable second half.
    for f in findings:
        is_lift = round((f["is_mean"] or 0) - base_is, 3) if f["is_mean"] is not None else None
        f["is_lift"] = is_lift
        f["robust"] = bool(is_lift is not None and is_lift > 0
                           and f["oos_lift"] and f["oos_lift"] > 0)
    findings.sort(key=lambda f: -(f["oos_lift"] or -99))
    return {"baseline": {"all": base_all, "is": base_is, "oos": base_oos, "split_at": cut},
            "n_rows": len(rows), "n_is": len(is_rows), "n_oos": len(oos_rows),
            "findings": findings}


def main():
    rows = build_rows()
    res = analyze(rows)
    res["updated"] = datetime.now(ET).strftime("%Y-%m-%d %H:%M %Z")
    json.dump(res, open(SUMMARY, "w"), indent=2, default=str)
    b = res["baseline"]
    print(f"[indicator] {res['n_rows']} gap setups  baseline ret%: all={b['all']:+.3f} "
          f"IS={b['is']:+.3f} OOS={b['oos']:+.3f}  (split @ {b['split_at']})", file=sys.stderr)
    print(f"\n{'INDICATOR':<26}{'best bucket':>12}{'n':>5}{'mean%':>8}{'liftAll':>8}{'OOSmean':>9}{'OOSlift':>8}  robust",
          file=sys.stderr)
    for f in res["findings"]:
        rob = "YES" if f["robust"] else ""
        om = f"{f['oos_mean']:+.3f}" if f["oos_mean"] is not None else "  n/a"
        print(f"{f['indicator']:<26}{str(f['best_bucket']):>12}{f['n']:>5}{f['mean_all']:>8.3f}"
              f"{f['lift_all']:>+8.3f}{om:>9}{f['oos_lift']:>+8.3f}  {rob}", file=sys.stderr)
    print(f"\n[indicator] wrote {SUMMARY}", file=sys.stderr)


if __name__ == "__main__":
    main()
