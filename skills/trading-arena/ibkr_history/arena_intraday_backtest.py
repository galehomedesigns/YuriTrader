#!/usr/bin/env python3
"""Phase 0 — continuous INTRADAY backtest of the arena bots on 2yr IBKR stock data.

Unlike bot_arena_stocks.py (which runs the bots only over the 30-min OPENING window
on gap-qualified names), this runs every bot CONTINUOUSLY through each full RTH
session over the multi-year IBKR cache — the way the live arena actually operates
(scan -> enter -> manage -> exit, all day). Purpose: see whether ANY arena bot has
a real, regime-stable edge on stocks BEFORE we build the live TV-CDP staging.

Faithful to the live arena:
  * Real bot classes (scan / should_enter / should_exit) — zero strategy re-impl.
  * Bots see 15-MINUTE bars (the live market_scanner feeds 15-min) — we resample
    the IBKR 2-min cache to 15-min RTH bars and warm indicators on the trailing
    WARMUP bars.
  * Same accounting: $STARTING_BALANCE, min(5%·bal,$50)/trade, MAX_CONCURRENT_POS,
    BOT_DAILY_LOSS_LIMIT, 15-min re-entry cooldown, STOCK_ROUNDTRIP_FEE_PCT.
  * Intraday only — flatten every position at the session close (no overnight).
  * Balance compounds across the whole window -> survivorship + an IS/OOS split
    (first half vs second half of the sessions) so a one-regime fluke is exposed.

Reads ONLY the IBKR cache (logs/backtest_cache_ibkr/ by default; override with
OPENING_ARENA_CACHE_DIR). No network. Writes logs/arena_intraday_summary.json.
Completely separate from the live system and from Opening Power.
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))            # skills/trading-arena

# Reuse the proven offline helpers from the opening arena harness.
from opening_agent.bot_arena_stocks import (            # noqa: E402
    make_bot, _asset, _new_state, _close, _load_bot_classes, START_BAL,
    MAX_POS_PCT, MAX_POS_USD, MAX_CONC, LOSS_LIMIT, COOLDOWN_S, WARMUP,
)

ET = ZoneInfo("America/New_York")
LOGS = os.path.join(os.path.dirname(_HERE), "logs")
CACHE_DIR = os.environ.get("OPENING_ARENA_CACHE_DIR") or os.path.join(LOGS, "backtest_cache_ibkr")
SUMMARY = os.path.join(LOGS, "arena_intraday_summary.json")
OPEN_T, CLOSE_T = time(9, 30), time(16, 0)


def resample_15m(bars):
    """2-min RTH bars -> 15-min bars aligned to :00/:15/:30/:45 (OHLC + summed vol)."""
    out, cur, bucket = [], None, None
    for b in bars:
        t = b["et"]
        if not (OPEN_T <= t.time() < CLOSE_T):
            continue
        bstart = t.replace(minute=(t.minute // 15) * 15, second=0, microsecond=0)
        if bucket is None or bstart != bucket:
            if cur:
                out.append(cur)
            bucket = bstart
            cur = {"et": bstart, "open": b["open"], "high": b["high"],
                   "low": b["low"], "close": b["close"], "volume": b["volume"]}
        else:
            cur["high"] = max(cur["high"], b["high"])
            cur["low"] = min(cur["low"], b["low"])
            cur["close"] = b["close"]
            cur["volume"] += b["volume"]
    if cur:
        out.append(cur)
    return out


def load_series_15m():
    """{sym: [15-min bars]} from the IBKR 2-min cache."""
    series = {}
    if not os.path.isdir(CACHE_DIR):
        return series
    for fn in os.listdir(CACHE_DIR):
        if not fn.endswith(".json") or fn.startswith("_"):
            continue
        try:
            raw = json.load(open(os.path.join(CACHE_DIR, fn)))
            bars = [{"et": datetime.fromisoformat(b["et"]), "open": b["open"], "high": b["high"],
                     "low": b["low"], "close": b["close"], "volume": b["volume"]}
                    for b in raw.get("bars", [])]
            bars.sort(key=lambda x: x["et"])
            b15 = resample_15m(bars)
            if len(b15) >= WARMUP + 26:                 # enough warmup + ~1 session
                series[fn[:-5]] = b15
        except Exception:                               # noqa: BLE001
            continue
    return series


def day_index(bars):
    """{date: [bar indices that day]} for one symbol's 15-min series."""
    by = defaultdict(list)
    for i, b in enumerate(bars):
        by[b["et"].date()].append(i)
    return by


def run_session(bot, st, day, steps, panel):
    """Drive one bot through one full RTH session; flatten at the close."""
    positions, daily_pnl, paused = {}, 0.0, False
    for ts in steps:
        md = panel.get(ts, {})
        if not md:
            continue
        for sym in list(positions):                     # exits first (native logic)
            if sym not in md:
                continue
            try:
                reason = bot.should_exit(positions[sym], md[sym])
            except Exception:                           # noqa: BLE001
                reason = None
            if reason:
                daily_pnl += _close(st, positions.pop(sym), md[sym].price, reason, ts, None)
        if daily_pnl <= LOSS_LIMIT:
            paused = True
        if paused or len(positions) >= MAX_CONC:
            continue
        try:
            cands = bot.scan(md) or []
        except Exception:                               # noqa: BLE001
            cands = []
        for sym in cands:
            if len(positions) >= MAX_CONC:
                break
            if sym in positions or sym not in md:
                continue
            if ts - st["last_close_ts"].get(sym, -1e18) < COOLDOWN_S:
                continue
            data = md[sym]
            try:
                ok = bot.should_enter(sym, data)
            except Exception:                           # noqa: BLE001
                ok = None
            if not ok or data.price <= 0:
                continue
            size = min(st["balance"] * MAX_POS_PCT, MAX_POS_USD)
            if size <= 0:
                continue
            positions[sym] = {"symbol": sym, "entry_price": data.price,
                              "qty": size / data.price, "side": "BUY", "stop": None,
                              "date": str(day), "gap": 0.0, "ind": {}}
    if positions:                                       # EOD flatten at the close
        last_md = panel.get(steps[-1], {}) if steps else {}
        for sym in list(positions):
            px = last_md[sym].price if sym in last_md else positions[sym]["entry_price"]
            _close(st, positions.pop(sym), px, "eod", steps[-1] if steps else 0, None)
    if daily_pnl <= LOSS_LIMIT:
        st["loss_limit_days"] += 1


def _scorecard(st):
    n = len(st["trades"])
    net = round(st["balance"] - START_BAL, 2)
    avg_pct = round(sum(t["pct"] for t in st["trades"]) / n, 3) if n else 0.0
    return {"trades": n, "end_balance": round(st["balance"], 2), "net": net,
            "return_pct": round(net / START_BAL * 100, 2),
            "win_rate": round(100 * st["wins"] / n, 1) if n else 0.0,
            "avg_pct_per_trade": avg_pct, "max_dd_pct": round(st["max_dd"], 2),
            "loss_limit_days": st["loss_limit_days"], "survived": st["balance"] >= START_BAL}


def main():
    series = load_series_15m()
    print(f"[arena-intraday] loaded {len(series)} symbols ({CACHE_DIR})", file=sys.stderr)
    if not series:
        print("[arena-intraday] no cache — nothing to do", file=sys.stderr)
        return
    # all sessions present across the universe
    all_days = sorted({d for bars in series.values() for d in {b["et"].date() for b in bars}})
    didx = {sym: day_index(bars) for sym, bars in series.items()}
    # tradeable days: skip the warmup-only early days (need WARMUP trailing bars)
    classes = _load_bot_classes()
    bots = {getattr(c, "NAME", c.__name__): make_bot(c) for c in classes}
    # IS/OOS split at the session midpoint
    cut = all_days[len(all_days) // 2]
    states = {(name, half): _new_state(name, half) for name in bots for half in ("ALL", "IS", "OOS")}

    for di, day in enumerate(all_days, 1):
        # build the day's panel once (shared across all bots)
        steps_set, panel = set(), defaultdict(dict)
        for sym, bars in series.items():
            idxs = didx[sym].get(day, [])
            if not idxs or idxs[0] < WARMUP:            # need full warmup before the session
                continue
            session_open = bars[idxs[0]]["open"]
            for i in idxs:
                ts = bars[i]["et"].timestamp()
                panel[ts][sym] = _asset(sym, bars, i, session_open)
                steps_set.add(ts)
        steps = sorted(steps_set)
        if not steps:
            continue
        half = "IS" if day < cut else "OOS"
        for name, bot in bots.items():
            for tag in ("ALL", half):
                run_session(bot, states[(name, tag)], day, steps, panel)
        if di % 20 == 0 or di == len(all_days):
            print(f"[arena-intraday] {di}/{len(all_days)} sessions ({day})", file=sys.stderr)

    rows = []
    for name in bots:
        rows.append({"bot": name,
                     "all": _scorecard(states[(name, "ALL")]),
                     "is": _scorecard(states[(name, "IS")]),
                     "oos": _scorecard(states[(name, "OOS")]),
                     # robust = positive AND survives in BOTH halves
                     "robust": (states[(name, "IS")]["balance"] > START_BAL and
                                states[(name, "OOS")]["balance"] > START_BAL)})
    rows.sort(key=lambda r: -r["all"]["end_balance"])
    summary = {
        "updated": datetime.now(ET).strftime("%Y-%m-%d %H:%M %Z"),
        "config": {"start_balance": START_BAL, "pos_size": f"min({MAX_POS_PCT*100:g}%·bal,${MAX_POS_USD:g})",
                   "max_concurrent": MAX_CONC, "daily_loss_limit": LOSS_LIMIT,
                   "bars": "15-min (resampled from IBKR 2-min)", "intraday_only": True,
                   "cache_dir": CACHE_DIR},
        "window": {"sessions": len(all_days), "start": str(all_days[0]), "end": str(all_days[-1]),
                   "is_oos_split": str(cut)},
        "coverage": {"symbols": len(series)},
        "results": rows,
    }
    os.makedirs(LOGS, exist_ok=True)
    json.dump(summary, open(SUMMARY, "w"), indent=2, default=str)
    print(f"[arena-intraday] wrote {SUMMARY}", file=sys.stderr)
    print(f"\n{'BOT':<18}{'trades':>7}{'end$':>9}{'ret%':>7}{'win%':>6}{'avg%':>7}{'robust':>8}", file=sys.stderr)
    for r in rows:
        a = r["all"]
        print(f"{r['bot']:<18}{a['trades']:>7}{a['end_balance']:>9.2f}{a['return_pct']:>7.2f}"
              f"{a['win_rate']:>6.1f}{a['avg_pct_per_trade']:>7.3f}{'YES' if r['robust'] else '':>8}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
