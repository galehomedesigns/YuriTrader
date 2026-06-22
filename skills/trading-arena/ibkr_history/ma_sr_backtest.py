#!/usr/bin/env python3
"""Research backtest — SMA-20/200 dynamic S&R + volume bounce, multi-timeframe.

A SEPARATE intraday research probe (not Opening Power, not the live arena) on the
2-year IBKR cache. Tests one clean, MECHANICAL hypothesis the user flagged: the
20/200 SMAs act as dynamic support; in an uptrend a pullback that touches the
SMA20 and bounces ON VOLUME is a long.

Strategy (fully deterministic — no params tuned on the sample):
  trend gate : SMA20 > SMA200            (uptrend)
  pullback   : bar low <= SMA20          (touched dynamic support)
  bounce     : bar close > SMA20         (rejected back above)
  volume     : bar vol >= VOL_MULT * SMA20(vol)   (participation)
  entry      : that bar's close
  stop       : that bar's low (below the bounce)
  target     : entry + TARGET_R * (entry-stop)
  exits      : target, stop, close<SMA20 (trend break), or session close (intraday)

Reports per timeframe: trades, win%, avg%/trade (fee-aware), and an IN-SAMPLE vs
OUT-OF-SAMPLE split + a by-hour breakdown — so a one-regime or one-hour fluke is
visible and we don't fool ourselves. Reads ONLY logs/backtest_cache_ibkr/.
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.environ.get("OPENING_ARENA_CACHE_DIR") or os.path.join(
    os.path.dirname(_HERE), "logs", "backtest_cache_ibkr")
OPEN_T, CLOSE_T = time(9, 30), time(16, 0)
VOL_MULT = 1.5
TARGET_R = 2.0
FEE_RT = float(os.environ.get("STOCK_ROUNDTRIP_FEE_PCT", "0.0010"))


def load_bars():
    out = {}
    for fn in os.listdir(CACHE_DIR):
        if not fn.endswith(".json") or fn.startswith("_"):
            continue
        try:
            raw = json.load(open(os.path.join(CACHE_DIR, fn)))
            b = [{"et": datetime.fromisoformat(x["et"]), "o": x["open"], "h": x["high"],
                  "l": x["low"], "c": x["close"], "v": x["volume"]} for x in raw.get("bars", [])]
            b.sort(key=lambda x: x["et"])
            out[fn[:-5]] = b
        except Exception:                                   # noqa: BLE001
            continue
    return out


def resample(bars, minutes):
    out, cur, bk = [], None, None
    for b in bars:
        t = b["et"]
        if not (OPEN_T <= t.time() < CLOSE_T):
            continue
        m = (t.hour * 60 + t.minute)
        start = (m // minutes) * minutes
        key = (t.date(), start)
        if bk != key:
            if cur:
                out.append(cur)
            bk = key
            cur = dict(b)
        else:
            cur["h"] = max(cur["h"], b["h"]); cur["l"] = min(cur["l"], b["l"])
            cur["c"] = b["c"]; cur["v"] += b["v"]
    if cur:
        out.append(cur)
    return out


def sma(vals, n, i):
    if i + 1 < n:
        return None
    return sum(vals[i - n + 1:i + 1]) / n


def run_symbol(bars, trades, variant="bounce20"):
    """Append (entry_date, entry_hour, ret_pct) trades for one symbol's TF series.

    variant:
      bounce20  — pullback to SMA20 + bounce + volume (uptrend)        [default]
      bounce200 — pullback to SMA200 + bounce + volume (uptrend)
      cross     — SMA20 crosses ABOVE SMA200 (golden cross); ride until
                  the cross reverses (death cross) / stop / session end
    """
    closes = [b["c"] for b in bars]
    vols = [b["v"] for b in bars]
    pos = None
    for i in range(len(bars)):
        b = bars[i]
        s20, s200, vavg = sma(closes, 20, i), sma(closes, 200, i), sma(vols, 20, i)
        # manage open position first
        if pos:
            exit_px = None
            if b["l"] <= pos["stop"]:
                exit_px = pos["stop"]
            elif pos.get("target") and b["h"] >= pos["target"]:
                exit_px = pos["target"]
            elif b["et"].time() >= time(15, 45) or b["et"].date() != pos["date"]:
                exit_px = b["c"]                            # intraday flatten
            elif variant == "cross":
                if s20 and s200 and s20 < s200:            # death cross → exit
                    exit_px = b["c"]
            else:
                lvl = sma(closes, 200 if variant == "bounce200" else 20, i)
                if lvl and b["c"] < lvl:
                    exit_px = b["c"]                        # trend break
            if exit_px is not None:
                ret = (exit_px - pos["entry"]) / pos["entry"] * 100 - FEE_RT * 100
                trades.append((pos["date"], pos["hour"], ret))
                pos = None
        if pos or not (s20 and s200 and vavg):
            continue
        if b["et"].time() >= time(15, 30):                  # too late to open intraday
            continue
        sig = False
        if variant == "bounce20":
            sig = s20 > s200 and b["l"] <= s20 and b["c"] > s20 and b["v"] >= VOL_MULT * vavg
            level = s20
        elif variant == "bounce200":
            sig = s20 > s200 and b["l"] <= s200 and b["c"] > s200 and b["v"] >= VOL_MULT * vavg
            level = s200
        elif variant == "cross":
            s20p, s200p = sma(closes, 20, i - 1), sma(closes, 200, i - 1)
            sig = bool(s20p and s200p and s20p <= s200p and s20 > s200)   # golden cross
            level = s200
        if not sig:
            continue
        entry = b["c"]
        stop = min(b["l"], level)                           # below the bounce/level
        if stop >= entry:
            continue
        pos = {"entry": entry, "stop": stop,
               "target": (entry + TARGET_R * (entry - stop)) if variant != "cross" else None,
               "date": b["et"].date(), "hour": b["et"].hour}
    return trades


def stats(ts):
    n = len(ts)
    if not n:
        return {"n": 0, "win": 0.0, "avg_pct": 0.0, "total_pct": 0.0}
    rets = [r for _, _, r in ts]
    return {"n": n, "win": round(100 * sum(1 for r in rets if r > 0) / n, 1),
            "avg_pct": round(sum(rets) / n, 3), "total_pct": round(sum(rets), 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", type=int, default=15, help="timeframe minutes (15 or 60)")
    ap.add_argument("--variant", default="bounce20", choices=["bounce20", "bounce200", "cross"])
    args = ap.parse_args()
    raw = load_bars()
    if not raw:
        print("no IBKR cache", file=sys.stderr)
        return
    all_trades = []
    for sym, bars in raw.items():
        run_symbol(resample(bars, args.tf), all_trades, args.variant)
    all_trades.sort(key=lambda t: t[0])
    days = sorted({d for d, _, _ in all_trades})
    cut = days[len(days) // 2] if days else None
    is_t = [t for t in all_trades if t[0] < cut]
    oos_t = [t for t in all_trades if t[0] >= cut]

    a, i, o = stats(all_trades), stats(is_t), stats(oos_t)
    robust = i["avg_pct"] > 0 and o["avg_pct"] > 0 and a["n"] >= 50
    print(f"\n=== {args.variant} | {args.tf}-min | {len(raw)} symbols ===")
    print(f"split @ {cut}  ({len(days)} trading days with signals)")
    print(f"{'cut':<8}{'n':>6}{'win%':>7}{'avg%/tr':>9}{'total%':>9}")
    for lab, s in (("ALL", a), ("IS", i), ("OOS", o)):
        print(f"{lab:<8}{s['n']:>6}{s['win']:>7}{s['avg_pct']:>9.3f}{s['total_pct']:>9.1f}")
    print(f"ROBUST (IS & OOS avg% > 0, n>=50): {'YES' if robust else 'NO'}")
    print("\nby entry hour (ET):")
    byh = defaultdict(list)
    for d, h, r in all_trades:
        byh[h].append(r)
    for h in sorted(byh):
        rs = byh[h]
        print(f"  {h:02d}:00  n={len(rs):>4}  win={100*sum(1 for r in rs if r>0)/len(rs):>4.0f}%  "
              f"avg={sum(rs)/len(rs):+.3f}%")


if __name__ == "__main__":
    main()
