#!/usr/bin/env python3
"""Stock Opening-Arena — run the 12 trading-arena bots on the 60-day opening data.

Mirrors the crypto arena (backtest.py) but on the EQUITY OPENING WINDOW:
  * Universe each session = ONLY stocks that meet the pre-market gap criteria
    (the discovered edge): opening gap in [OPENING_SCAN_MIN_GAP_PCT,
    OPENING_SCAN_MAX_GAP_PCT]% vs the prior session close.
  * Trading is confined to 9:30 -> the OPENING_SESSION_CUTOFF_MIN cutoff (the
    same 20-min-ish window). Anything still open at the cutoff is force-flattened.
  * Each bot starts with $STARTING_BALANCE ($1000), sizes min(5%·balance, $50)/
    trade, max 3 concurrent, -$30 daily-loss-limit, 15-min re-entry cooldown —
    the SAME live config. Balance compounds across the 60 days -> survivorship.

Two exit variants per bot (per the run-both decision):
  A (native)   — bot's own should_exit(), force-flattened at the cutoff.
  B (standard) — bot only signals ENTRY; exit is the standard opening one-bar
                 stop (bar-1 low) or the cutoff. Isolates entry edge under one exit.

Faithfulness: real bot classes (scan/should_enter/should_exit), real AssetData +
compute_indicators (warmed on ~210 trailing bars from the cached continuous RTH
series), real accounting constants. No reimplementation of strategy logic.
Stock round-trip fee/slippage = STOCK_ROUNDTRIP_FEE_PCT (~0.10%) applied per trade.

Reads ONLY the cached candles (logs/backtest_cache/) — no network. Writes its own
summary (logs/bot_arena_stocks_summary.json) + per-trade log for the indicator
search. Independent of the live system.
"""
import json
import os
import sys
import importlib
from collections import defaultdict
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))            # skills/trading-arena


def _load_env():
    p = "/home/tonygale/openclaw/.env"
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if k and v:
                    os.environ.setdefault(k, v)


_load_env()
from shared.market_scanner import AssetData                  # noqa: E402
from opening_agent import classifier as C                    # noqa: E402
import config as CFG                                          # noqa: E402

ET = ZoneInfo("America/New_York")
LOGS = os.path.join(os.path.dirname(_HERE), "logs")
CACHE_DIR = os.path.join(LOGS, "backtest_cache")
SUMMARY = os.path.join(LOGS, "bot_arena_stocks_summary.json")
TRADELOG = os.path.join(LOGS, "bot_arena_stocks_trades.jsonl")

OPEN_T = time(9, 30)
CUTOFF_MIN = int(os.environ.get("OPENING_SESSION_CUTOFF_MIN", "30"))
GAP_MIN = float(os.environ.get("OPENING_SCAN_MIN_GAP_PCT", "1"))
GAP_MAX = float(os.environ.get("OPENING_SCAN_MAX_GAP_PCT", "25"))
WARMUP = 210                                           # trailing bars for indicators
START_BAL = float(getattr(CFG, "STARTING_BALANCE", 1000.0))
MAX_POS_PCT = float(getattr(CFG, "MAX_POSITION_PCT", 0.05))
MAX_POS_USD = float(getattr(CFG, "MAX_POSITION_USD", 50.0))
MAX_CONC = int(getattr(CFG, "MAX_CONCURRENT_POS", 3))
LOSS_LIMIT = float(getattr(CFG, "BOT_DAILY_LOSS_LIMIT", -30.0))
COOLDOWN_S = float(getattr(CFG, "RE_ENTRY_COOLDOWN_MINUTES", 15)) * 60
FEE_RT = float(os.environ.get("STOCK_ROUNDTRIP_FEE_PCT", "0.0010"))

BOTS = [
    ("the_reverter", "TheReverter"), ("volume_whisperer", "VolumeWhisperer"),
    ("correlation_hunter", "CorrelationHunter"), ("momentum_burst", "MomentumBurst"),
    ("news_sniper", "NewsSniper"), ("squeeze_breaker", "SqueezeBreaker"),
    ("momentum_hunter", "MomentumHunter"), ("nano_sniper", "NanoSniper"),
    ("flag_rider", "FlagRider"), ("trend_rider", "TrendRider"),
    ("trend_breakout", "TrendBreakout"), ("trap_catcher", "TrapCatcher"),
]


def _load_bot_classes():
    out = []
    for mod, cls in BOTS:
        try:
            m = importlib.import_module(f"bots.{mod}")
            out.append(getattr(m, cls))
        except Exception as e:                          # noqa: BLE001
            print(f"[arena] could not import {cls}: {e}", file=sys.stderr)
    return out


def make_bot(cls):
    """Instantiate without PaperTrader/Supabase (crypto backtest pattern)."""
    b = cls.__new__(cls)
    b.paused = False
    b.pause_reason = ""
    b._pair_ratios = {}
    b._pair_zscores = {}
    b._last_tay = {}
    return b


# ── Data: load cached series, build the gap-qualified daily panels ─────────────
def load_series():
    series = {}
    if not os.path.isdir(CACHE_DIR):
        return series
    for fn in os.listdir(CACHE_DIR):
        if not fn.endswith(".json") or fn.startswith("_") or fn == "news":
            continue
        sym = fn[:-5].replace("_", ":") if ":" in fn else fn[:-5]
        try:
            raw = json.load(open(os.path.join(CACHE_DIR, fn)))
            bars = [{"et": datetime.fromisoformat(b["et"]), "open": b["open"],
                     "high": b["high"], "low": b["low"], "close": b["close"],
                     "volume": b["volume"]} for b in raw.get("bars", [])]
            if len(bars) >= WARMUP + 5:
                series[fn[:-5]] = sorted(bars, key=lambda x: x["et"])
        except Exception:                               # noqa: BLE001
            continue
    return series


def gap_universe(series):
    """{day: [(symbol, open_idx, gap)]} for sessions whose opening gap is in band."""
    by_day = defaultdict(list)
    for sym, bars in series.items():
        # index bars by day; find each day's first >=9:30 bar (the opening bar)
        days = defaultdict(list)
        for i, b in enumerate(bars):
            days[b["et"].date()].append(i)
        prev_close = None
        for d in sorted(days):
            idxs = days[d]
            oi = next((i for i in idxs if bars[i]["et"].time() >= OPEN_T), None)
            if oi is not None and prev_close and oi >= WARMUP:
                gap = (bars[oi]["open"] - prev_close) / prev_close * 100
                if GAP_MIN <= gap <= GAP_MAX:
                    by_day[d].append((sym, oi, round(gap, 2)))
            prev_close = bars[idxs[-1]]["close"]        # last bar of the day
    return by_day


def _asset(sym, bars, i, session_open):
    """AssetData from the trailing WARMUP bars ending at bar i; day_change vs the
    session's 9:30 open (intraday move the bots expect)."""
    lo = max(0, i - WARMUP + 1)
    win = bars[lo:i + 1]
    d = AssetData(symbol=sym, asset_type="stock")
    d.closes = [b["close"] for b in win]
    d.highs = [b["high"] for b in win]
    d.lows = [b["low"] for b in win]
    d.opens = [b["open"] for b in win]
    d.volumes = [b["volume"] for b in win]
    d.price = d.closes[-1]
    d.open, d.high, d.low = win[-1]["open"], win[-1]["high"], win[-1]["low"]
    d.volume = win[-1]["volume"]
    d.day_change_pct = ((d.price - session_open) / session_open * 100) if session_open else 0.0
    try:
        d.compute_indicators()
    except Exception:                                   # noqa: BLE001
        pass
    return d


def build_day_panel(series, day, members):
    """For one session: ordered step timestamps + {step_epoch: {sym: AssetData}}
    over the 9:30->cutoff window, plus per-symbol bar-1 stop (for variant B)."""
    cutoff_ts = datetime.combine(day, OPEN_T, ET).timestamp() + CUTOFF_MIN * 60
    panel, stops, session_opens = defaultdict(dict), {}, {}
    step_set = set()
    for sym, oi, _gap in members:
        bars = series[sym]
        session_open = bars[oi]["open"]
        session_opens[sym] = session_open
        bar1 = bars[oi]
        stops[sym] = C.stop_level_long(bar1)
        i = oi
        while i < len(bars) and bars[i]["et"].timestamp() <= cutoff_ts + 1:
            ts = bars[i]["et"].timestamp()
            panel[ts][sym] = _asset(sym, bars, i, session_open)
            step_set.add(ts)
            i += 1
    return sorted(step_set), panel, stops, cutoff_ts


# ── Accounting ─────────────────────────────────────────────────────────────────
def _new_state(name, variant):
    return {"name": name, "variant": variant, "balance": START_BAL, "peak": START_BAL,
            "max_dd": 0.0, "trades": [], "wins": 0, "losses": 0, "loss_limit_days": 0,
            "last_close_ts": {}}


def _close(st, pos, exit_px, reason, ts, tradelog):
    qty = pos["qty"]
    gross = (exit_px - pos["entry_price"]) * qty
    fee = FEE_RT * qty * pos["entry_price"]             # ~0.10% round-trip
    pnl = gross - fee
    st["balance"] += pnl
    st["peak"] = max(st["peak"], st["balance"])
    st["max_dd"] = max(st["max_dd"], (st["peak"] - st["balance"]) / st["peak"] * 100)
    pct = (exit_px - pos["entry_price"]) / pos["entry_price"] * 100
    st["wins" if pnl > 0 else "losses"] += 1
    st["trades"].append({"pnl": round(pnl, 4), "pct": round(pct, 3)})
    st["last_close_ts"][pos["symbol"]] = ts
    if tradelog is not None:
        tradelog.append({"bot": st["name"], "variant": st["variant"],
                         "symbol": pos["symbol"], "date": pos["date"],
                         "gap": pos["gap"], "entry": round(pos["entry_price"], 4),
                         "exit": round(exit_px, 4), "pct": round(pct, 3),
                         "pnl": round(pnl, 4), "reason": reason,
                         "ind": pos.get("ind", {})})
    return pnl


def run_day(bot, st, day, steps, panel, stops, cutoff_ts, variant, tradelog):
    positions, daily_pnl, paused = {}, 0.0, False
    for ts in steps:
        md = panel.get(ts, {})
        if not md:
            continue
        # exits before entries
        for sym in list(positions):
            if sym not in md:
                continue
            data = md[sym]
            reason = None
            if variant == "A":
                try:
                    reason = bot.should_exit(positions[sym], data)
                except Exception:                       # noqa: BLE001
                    reason = None
            else:                                       # B: protective one-bar stop
                s = positions[sym].get("stop")          # None unless it's BELOW entry
                if s is not None and data.low <= s:
                    reason = "stop"
            if reason:
                exit_px = positions[sym]["stop"] if (variant == "B" and reason == "stop") else data.price
                daily_pnl += _close(st, positions.pop(sym), exit_px, reason, ts, tradelog)
        if daily_pnl <= LOSS_LIMIT:
            paused = True
        # entries
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
                continue                                # re-entry cooldown
            data = md[sym]
            try:
                ok = bot.should_enter(sym, data)
            except Exception:                           # noqa: BLE001
                ok = None
            if not ok:
                continue
            size = min(st["balance"] * MAX_POS_PCT, MAX_POS_USD)
            if size <= 0 or data.price <= 0:
                continue
            raw_stop = stops.get(sym, 0.0)
            # A protective stop must sit BELOW entry. Bots can enter below the
            # bar-1 low, where that stop would be above entry (not a stop at all) —
            # in that case ride to the cutoff instead of booking a phantom "stop" gain.
            eff_stop = raw_stop if (raw_stop and raw_stop < data.price) else None
            positions[sym] = {
                "symbol": sym, "entry_price": data.price, "qty": size / data.price,
                "side": "BUY", "stop": eff_stop, "date": str(day),
                "gap": st.get("_gmap", {}).get(sym, 0.0),
                "ind": {"rvol": getattr(data, "rvol", None), "rsi": getattr(data, "rsi_14", None),
                        "adx": getattr(data, "adx_14", None), "atr": getattr(data, "atr_14", None),
                        "macd_bull": getattr(data, "macd_bullish", None),
                        "day_change": round(getattr(data, "day_change_pct", 0) or 0, 3),
                        "above_vwap": (data.price > getattr(data, "vwap_val", 0))
                        if getattr(data, "vwap_val", None) else None}}
    # cutoff flatten — close anything still open at the last step's price
    if positions:
        last_md = panel.get(steps[-1], {}) if steps else {}
        for sym in list(positions):
            px = last_md[sym].price if sym in last_md else positions[sym]["entry_price"]
            _close(st, positions.pop(sym), px, "cutoff", cutoff_ts, tradelog)
    if daily_pnl <= LOSS_LIMIT:
        st["loss_limit_days"] += 1


def main():
    series = load_series()
    print(f"[arena] loaded {len(series)} symbols from cache", file=sys.stderr)
    universe = gap_universe(series)
    days = sorted(universe)
    nmatch = sum(len(v) for v in universe.values())
    print(f"[arena] gap universe: {len(days)} sessions, {nmatch} gap-qualified symbol-days "
          f"(gap {GAP_MIN:g}-{GAP_MAX:g}%)", file=sys.stderr)

    classes = _load_bot_classes()
    states = {}
    for cls in classes:
        name = getattr(cls, "NAME", cls.__name__)
        for v in ("A", "B"):
            states[(name, v)] = _new_state(name, v)
    bots = {name: make_bot(cls) for cls in classes for name in [getattr(cls, "NAME", cls.__name__)]}
    tradelog = []

    for di, day in enumerate(days, 1):
        steps, panel, stops, cutoff_ts = build_day_panel(series, day, universe[day])
        # attach gap to positions via a per-day symbol->gap map
        gmap = {s: g for s, _, g in universe[day]}
        for (name, v), st in states.items():
            # patch: inject gap into entries by wrapping stops dict access — simplest is
            # to set on state for run_day to read
            st["_gmap"] = gmap
            run_day(bots[name], st, day, steps, panel, stops, cutoff_ts, v, tradelog)
        if di % 5 == 0 or di == len(days):
            print(f"[arena] {di}/{len(days)} sessions processed ({day})", file=sys.stderr)

    # write trade log (for the indicator search)
    os.makedirs(LOGS, exist_ok=True)
    with open(TRADELOG, "w") as f:
        for t in tradelog:
            f.write(json.dumps(t) + "\n")

    # scorecards
    rows = []
    for (name, v), st in states.items():
        n = len(st["trades"])
        net = round(st["balance"] - START_BAL, 2)
        win = round(100 * st["wins"] / n, 1) if n else 0.0
        rows.append({"bot": name, "variant": v, "trades": n,
                     "end_balance": round(st["balance"], 2), "net": net,
                     "return_pct": round(net / START_BAL * 100, 2), "win_rate": win,
                     "max_dd_pct": round(st["max_dd"], 2),
                     "loss_limit_days": st["loss_limit_days"],
                     "survived": st["balance"] >= START_BAL})
    rows.sort(key=lambda r: -r["end_balance"])
    summary = {
        "updated": datetime.now(ET).strftime("%Y-%m-%d %H:%M %Z"),
        "config": {"start_balance": START_BAL, "pos_size": f"min({MAX_POS_PCT*100:g}%·bal, ${MAX_POS_USD:g})",
                   "max_concurrent": MAX_CONC, "daily_loss_limit": LOSS_LIMIT,
                   "cooldown_min": COOLDOWN_S / 60, "fee_rt_pct": FEE_RT * 100,
                   "gap_band": f"{GAP_MIN:g}-{GAP_MAX:g}%", "cutoff_min": CUTOFF_MIN,
                   "window": {"sessions": len(days),
                              "start": str(days[0]) if days else None,
                              "end": str(days[-1]) if days else None}},
        "coverage": {"symbols": len(series), "gap_symbol_days": nmatch, "total_bot_trades": len(tradelog)},
        "results": rows,
    }
    json.dump(summary, open(SUMMARY, "w"), indent=2, default=str)
    print(f"[arena] wrote {SUMMARY}", file=sys.stderr)
    print(f"\n{'BOT':<18}{'V':>2}{'trades':>7}{'end$':>9}{'ret%':>7}{'win%':>6}{'maxDD%':>7}{'LLdays':>7}",
          file=sys.stderr)
    for r in rows:
        print(f"{r['bot']:<18}{r['variant']:>2}{r['trades']:>7}{r['end_balance']:>9.2f}"
              f"{r['return_pct']:>7.2f}{r['win_rate']:>6.1f}{r['max_dd_pct']:>7.2f}{r['loss_limit_days']:>7}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
