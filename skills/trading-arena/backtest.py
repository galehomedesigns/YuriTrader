#!/usr/bin/env python3
"""Edge-research backtest harness for the trading arena.

Answers one question with ground truth: does ANY of the 10 bots have a
positive net-of-fee expectancy at a timeframe where Kraken's round-trip fee
doesn't dominate? Built for the 2026-05-17 "develop a system that works"
investigation.

FAITHFULNESS CONTRACT (so results aren't fiction):
  * Uses the REAL bot classes and the REAL AssetData.compute_indicators() —
    zero reimplementation of strategy or indicator logic.
  * Replicates production market_scanner.fetch_crypto_data exactly: a 50-bar
    trailing window (bars[-50:]), price = window close.
  * Replicates production base_bot.evaluate(): a time-aligned panel of all 6
    pairs is passed as market_data each step; exits checked before entries;
    one open position per symbol per bot (has_position guard); long-only.
    One long-lived bot instance per interval so stateful bots (Correlation
    Hunter's z-score history) accumulate exactly as in production.
  * No look-ahead: indicators at step t use only bars <= t; the decision at
    step t acts on bar t's close (a live scan cycle sees the just-closed bar).
  * Bots built via __new__ (no PaperTrader / Supabase / network); only pure
    strategy methods run. Per-bot dict state (_pair_ratios) is initialised.
  * Simplification vs production: the 3-concurrent-position cap is NOT
    enforced (it throttles throughput, not per-trade edge — what we measure).
    Correlation Hunter only has its BTC/ETH leg here (SPY/AAPL/GLD are stocks).

VALIDATION: the 15-min block is the control. If its net expectancy ≈ the
known live result (deeply negative, ~ -0.7%/trade) the harness is trusted
and the 60/240-min numbers are credible.

Usage:  .venv/bin/python skills/trading-arena/backtest.py
"""
from __future__ import annotations

import json
import statistics as st
import sys
import time
import urllib.request
from pathlib import Path

SKILL = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL))

from config import KRAKEN_PAIRS  # noqa: E402
from shared.market_scanner import AssetData  # noqa: E402
from bots.trap_catcher import TrapCatcher  # noqa: E402
from bots.squeeze_breaker import SqueezeBreaker  # noqa: E402
from bots.momentum_hunter import MomentumHunter  # noqa: E402
from bots.correlation_hunter import CorrelationHunter  # noqa: E402
from bots.the_reverter import TheReverter  # noqa: E402
from bots.trend_rider import TrendRider  # noqa: E402
from bots.flag_rider import FlagRider  # noqa: E402
from bots.volume_whisperer import VolumeWhisperer  # noqa: E402
from bots.nano_sniper import NanoSniper  # noqa: E402
from bots.news_sniper import NewsSniper  # noqa: E402
from bots.trend_breakout import TrendBreakout  # noqa: E402

BOT_CLASSES = [
    TrapCatcher, SqueezeBreaker, MomentumHunter, CorrelationHunter,
    TheReverter, TrendRider, FlagRider, VolumeWhisperer, NanoSniper, NewsSniper,
    TrendBreakout,  # 2026-05-17: the deliberately low-frequency, large-target design
]

WINDOW = 50           # production keeps bars[-50:]
TAKER_RT = 0.8        # % round-trip taker fee (market orders, today)
MAKER_RT = 0.5        # % round-trip if entries/exits were post-only limit
LIVE_NOTIONAL = 25.0  # $ per live trade (current cap)
INTERVALS = {15: 96.0, 60: 24.0, 240: 6.0, 1440: 1.0}  # interval-min -> bars/day


def make_bot(cls):
    """Instantiate without BaseBot.__init__ (no PaperTrader / no network)."""
    b = cls.__new__(cls)
    b.paused = False
    b.pause_reason = ""
    b._pair_ratios = {}   # Correlation Hunter state (harmless on others)
    b._pair_zscores = {}
    return b


def fetch_ohlc(pair: str, interval: int) -> list:
    """Kraken public OHLC: [[t,o,h,l,c,vwap,vol,cnt], ...] oldest→newest.
    Kraken caps at ~720 most-recent bars regardless of `since`."""
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (YuriBacktest/1.0)"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    if data.get("error"):
        raise RuntimeError(f"{pair}: {data['error']}")
    return next(v for k, v in data["result"].items() if k != "last")


def build_panel(interval: int):
    """Return (timeline, per_sym) where timeline is the sorted intersection of
    bar timestamps across all pairs, and per_sym[sym] maps ts -> AssetData
    (computed from that sym's own trailing 50-bar window at that ts)."""
    per_sym_ts_to_ad: dict[str, dict[int, AssetData]] = {}
    ts_sets = []
    spans = []
    for sym, pair in KRAKEN_PAIRS.items():
        try:
            raw = fetch_ohlc(pair, interval)
        except Exception as e:
            print(f"  {sym:<9} {interval:>3}m: FETCH FAILED {e}")
            continue
        time.sleep(1.0)
        ts = [int(b[0]) for b in raw]
        closes = [float(b[4]) for b in raw]
        highs = [float(b[2]) for b in raw]
        lows = [float(b[3]) for b in raw]
        opens = [float(b[1]) for b in raw]
        vols = [float(b[6]) for b in raw]
        m: dict[int, AssetData] = {}
        for i in range(WINDOW - 1, len(raw)):  # only warmed-up bars
            lo = i - WINDOW + 1
            d = AssetData(symbol=sym, asset_type="crypto")
            d.closes = closes[lo:i + 1]
            d.highs = highs[lo:i + 1]
            d.lows = lows[lo:i + 1]
            d.opens = opens[lo:i + 1]
            d.volumes = vols[lo:i + 1]
            d.price = d.closes[-1]
            d.open = d.opens[-1]
            d.high = d.highs[-1]
            d.low = d.lows[-1]
            d.day_change_pct = ((d.price - d.open) / d.open * 100) if d.open else 0
            d.compute_indicators()
            m[ts[i]] = d
        per_sym_ts_to_ad[sym] = m
        ts_sets.append(set(m.keys()))
        spans.append(len(raw) / INTERVALS[interval])
        print(f"  {sym:<9} {interval:>3}m: {len(raw):>4} bars (~{len(raw)/INTERVALS[interval]:.0f}d)")
    if not ts_sets:
        return [], {}, 0.0
    timeline = sorted(set.intersection(*ts_sets))
    return timeline, per_sym_ts_to_ad, (st.mean(spans) if spans else 0.0)


def run_bot(bot, timeline, per_sym) -> list[float]:
    """Faithful evaluate() replay across the aligned timeline.
    Returns realized GROSS % moves (fees applied later)."""
    trades: list[float] = []
    positions: dict[str, dict] = {}
    syms = list(per_sym.keys())
    for tstamp in timeline:
        md = {s: per_sym[s][tstamp] for s in syms if tstamp in per_sym[s]}
        if not md:
            continue
        # Step 1: exits before entries (production order)
        for s in list(positions.keys()):
            if s in md:
                if bot.should_exit(positions[s], md[s]):
                    e = positions[s]["entry_price"]
                    trades.append((md[s].price - e) / e * 100)
                    del positions[s]
        # Step 2: scan + enter
        try:
            cands = bot.scan(md)
        except Exception:
            cands = []
        for s in cands:
            if s in positions or s not in md:
                continue
            try:
                if bot.should_enter(s, md[s]):
                    positions[s] = {"entry_price": md[s].price, "symbol": s,
                                    "side": "BUY", "qty": 1, "paper": True}
            except Exception:
                pass
    return trades


def stats(gross: list[float]):
    n = len(gross)
    if n == 0:
        return None
    out = {"n": n, "mean_gross": st.mean(gross),
           "win_raw": sum(1 for g in gross if g > 0) / n * 100}
    for tag, fee in (("taker", TAKER_RT), ("maker", MAKER_RT)):
        net = [g - fee for g in gross]
        ne = st.mean(net)
        sd = st.pstdev(net) if n > 1 else 0.0
        se = sd / (n ** 0.5) if n else 0.0
        out[tag] = {"net_e": ne, "ci_lo": ne - 1.96 * se, "ci_hi": ne + 1.96 * se,
                    "win": sum(1 for g in gross if g > fee) / n * 100,
                    "sig_pos": (ne - 1.96 * se) > 0}
    return out


def main():
    print("Kraken public OHLC, 6 pairs × {15,60,240}m (~720 bars = Kraken max):\n")
    for iv in INTERVALS:
        timeline, per_sym, mean_span = build_panel(iv)
        tf = {15: "15-MIN  (CONTROL — must reproduce the deeply-negative live result)",
              60: "1-HOUR", 240: "4-HOUR", 1440: "DAILY (~2yr, best sample for low-freq)"}[iv]
        print(f"\n{'='*94}\n{tf}   aligned timeline={len(timeline)} bars, "
              f"~{mean_span:.0f}d/pair, {len(per_sym)} pairs\n{'='*94}")
        if not timeline:
            print("  no aligned data — skipped")
            continue
        active_pairs = len(per_sym)
        total_pair_days = mean_span * active_pairs
        hdr = (f"{'bot':<18}{'n':>4}{'t/day':>6}{'meanG%':>8}"
               f"{'netE%tk':>9}{'95lo':>7}{'netE%mk':>9}{'95lo':>7}"
               f"{'$/d tk/mk':>13}  verdict")
        print(hdr + "\n" + "-" * len(hdr))
        winners = []
        for cls in BOT_CLASSES:
            g = run_bot(make_bot(cls), timeline, per_sym)
            s = stats(g)
            if not s:
                print(f"{cls.NAME:<18}{0:>4}   (no trades)")
                continue
            tpd = s["n"] / total_pair_days if total_pair_days else 0.0
            tk, mk = s["taker"], s["maker"]
            dd_tk = tk["net_e"] / 100 * LIVE_NOTIONAL * tpd
            dd_mk = mk["net_e"] / 100 * LIVE_NOTIONAL * tpd
            verdict = ("EDGE" if mk["sig_pos"]
                       else "maybe+" if mk["net_e"] > 0 else "—")
            print(f"{cls.NAME:<18}{s['n']:>4}{tpd:>6.1f}{s['mean_gross']:>8.3f}"
                  f"{tk['net_e']:>9.3f}{tk['ci_lo']:>7.2f}"
                  f"{mk['net_e']:>9.3f}{mk['ci_lo']:>7.2f}"
                  f"{dd_tk:>6.2f}/{dd_mk:>5.2f}  {verdict}")
            if mk["sig_pos"]:
                winners.append((cls.NAME, s, tpd, dd_mk))
        if winners:
            print("\n  >>> STATISTICALLY POSITIVE (maker, 95% CI lower bound > 0):")
            for nm, s, tpd, dmk in winners:
                print(f"      {nm}: net {s['maker']['net_e']:+.3f}%/trade, "
                      f"95% CI [{s['maker']['ci_lo']:+.3f},{s['maker']['ci_hi']:+.3f}], "
                      f"n={s['n']}, ~{tpd:.1f}/day, ~${dmk:+.2f}/day @ $25")
        else:
            print("\n  >>> none statistically positive at this timeframe.")


if __name__ == "__main__":
    main()
