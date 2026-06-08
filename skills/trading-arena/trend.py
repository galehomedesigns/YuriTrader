#!/usr/bin/env python3
"""
TREND RIDER — a deliberately SLOW, readable trend-participation tool.

This is the honest version of "buy crypto, ride it up, step aside when it
turns over." It does NOT scalp. It does NOT read 50 indicators. It makes a
handful of decisions per YEAR on DAILY candles, so the Kraken fee is a
rounding error instead of the thing that bleeds the account.

  WHAT MAKES MONEY HERE: crypto trending up, and you being in it.
  WHAT THIS AVOIDS: paying a 0.8-1.6% round-trip fee dozens of times chasing
                    wiggles that have no edge.

THE ENTIRE RULE (read this — it is the whole strategy):
  * Work on DAILY closes only.
  * "Uptrend confirmed" = today's close is ABOVE the SLOW moving average,
    AND the FAST moving average is above the SLOW one.
  * FLAT (holding USD) and uptrend confirmed   -> BUY (go long).
  * LONG and close drops BELOW the SLOW average -> SELL (trend broke).
  * LONG and price falls more than TRAIL_STOP_PCT from its peak since you
    bought (only if TRAIL_STOP_PCT > 0)        -> SELL (lock the gain).
  * Otherwise: do nothing. Most days you do nothing. That is the point.

Three commands:
  backtest : replay ~2 years of REAL Kraken daily candles, no look-ahead,
             show every trade, and compare to just buying & holding.
  signal   : fetch the latest data, print exactly where we stand and what a
             run RIGHT NOW would do, and why — in plain English.
  signal --execute : also place that buy/sell on Kraken (DRY-RUN unless the
             KRAKEN_ALLOW_TRADING=true env gate is open — same safety
             convention the rest of the arena uses).

Honest limits, stated up front:
  * Kraken's public API only gives ~720 daily bars (~2 years). That is ONE
    market regime. A good backtest here is encouraging, NOT a guarantee.
  * This makes money only if the asset trends up over the period. No moving
    average predicts the future; it just gets you out faster than holding
    through a crash. That is the only thing it does.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────
#  TUNE THESE — every knob the strategy has is right here, in plain sight.
# ─────────────────────────────────────────────────────────────────────────
PAIR             = "BTC/USD"   # any of the pairs in PAIRS below
FAST             = 20          # fast moving-average length, in DAYS
SLOW             = 100         # slow moving-average length, in DAYS (the trend)
TRAIL_STOP_PCT   = 0.0         # 0 = off. e.g. 20 = sell if price falls 20% off its peak while long
BUFFER_PCT       = 3.0         # anti-whipsaw: only exit when the close is THIS % BELOW the slow
                               #   MA, not a hair below it. Creates a dead-band, so price grazing
                               #   the line back and forth no longer triggers a fee-paying churn.
FEE_PCT_PER_SIDE = 0.40        # MEASURED Kraken maker fee 2026-05-18. Use 0.80 for market/taker.

# Friendly name -> Kraken OHLC pair code. Mirrors config.KRAKEN_PAIRS.
PAIRS = {
    "BTC/USD": "XXBTZUSD", "ETH/USD": "XETHZUSD", "SOL/USD": "SOLUSD",
    "XRP/USD": "XXRPZUSD", "ADA/USD": "ADAUSD",   "DOGE/USD": "XDGUSD",
}
STATE_PATH = Path.home() / ".trend_state.json"   # one open position, inspectable JSON
SKILL = Path(__file__).resolve().parent


# ───────────────────────────── data ─────────────────────────────────────
def fetch_candles(pair_code: str, interval: int = 1440):
    """Real Kraken public OHLC, oldest→newest. No API key needed. `interval`
    is minutes: 1440 = daily, 10080 = weekly. Kraken caps the response at
    ~720 bars regardless of how far back you ask — so weekly reaches ~14
    years (spanning the 2018 + 2022 bear markets) where daily only ~2."""
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair_code}&interval={interval}"
    req = urllib.request.Request(url, headers={"User-Agent": "TrendRider/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    if data.get("error"):
        raise RuntimeError(f"Kraken error for {pair_code}: {data['error']}")
    raw = next(v for k, v in data["result"].items() if k != "last")
    # each bar: [time, open, high, low, close, vwap, volume, count]
    return [(int(b[0]), float(b[4])) for b in raw]   # (timestamp, close)


def sma(closes, end_idx, length):
    """Simple moving average of the `length` closes ending AT end_idx
    (inclusive). Uses only past+current data — never the future."""
    if end_idx + 1 < length:
        return None
    window = closes[end_idx + 1 - length: end_idx + 1]
    return sum(window) / length


# ─────────────────────────── the strategy ───────────────────────────────
def _tf(weekly: bool):
    """Resolve the timeframe. The strategy's ECONOMIC horizon is held
    constant across timeframes — weekly MA lengths are the daily ones
    divided by the fixed calendar factor of 5 trading days per week
    (100d→20w, 20d→4w). BUFFER_PCT and FEE are UNCHANGED. Nothing is
    re-optimised for weekly; this is the same strategy, longer history."""
    if weekly:
        return {"interval": 10080, "fast": max(1, round(FAST / 5)),
                "slow": max(1, round(SLOW / 5)), "unit": "w", "label": "weekly"}
    return {"interval": 1440, "fast": FAST, "slow": SLOW,
            "unit": "d", "label": "daily"}


def replay(candles, fast=FAST, slow=SLOW, unit="d"):
    """Walk every bar in order, applying THE RULE with no look-ahead.

    Returns:
      trades  : list of closed round-trips, each a dict with entry/exit
      open_pos: the still-open position at the end (dict) or None
      bars    : list of (date_str, close) for the warmed-up period (for B&H)
    """
    closes = [c for _, c in candles]
    dates  = [datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
              for t, _ in candles]

    trades, open_pos, bars = [], None, []
    for i in range(len(closes)):
        s = sma(closes, i, slow)
        f = sma(closes, i, fast)
        if s is None or f is None:
            continue                       # not enough history yet — wait
        price = closes[i]
        bars.append((dates[i], price))

        if open_pos is None:
            # FLAT: enter only on a confirmed uptrend.
            if price > s and f > s:
                open_pos = {"entry_date": dates[i], "entry_price": price,
                            "peak": price, "i": i}
        else:
            open_pos["peak"] = max(open_pos["peak"], price)
            exit_line = s * (1 - BUFFER_PCT / 100)
            reason = None
            if price < exit_line:
                reason = (f"close ${price:,.2f} fell {BUFFER_PCT}% below {slow}{unit} MA "
                          f"(exit line ${exit_line:,.2f})")
            elif TRAIL_STOP_PCT > 0 and price <= open_pos["peak"] * (1 - TRAIL_STOP_PCT / 100):
                reason = (f"trailing stop: ${price:,.2f} is {TRAIL_STOP_PCT}% "
                          f"below peak ${open_pos['peak']:,.2f}")
            if reason:
                e = open_pos["entry_price"]
                gross = (price - e) / e * 100
                net = gross - 2 * FEE_PCT_PER_SIDE
                trades.append({**open_pos, "exit_date": dates[i],
                               "exit_price": price, "gross": gross,
                               "net": net, "exit_reason": reason})
                open_pos = None
    return trades, open_pos, bars


def current_view(candles, fast=FAST, slow=SLOW, unit="d"):
    """Plain-English read of where the rule stands on the LAST bar, and the
    action a run right now would take."""
    closes = [c for _, c in candles]
    i = len(closes) - 1
    s, f = sma(closes, i, slow), sma(closes, i, fast)
    price = closes[i]
    date = datetime.fromtimestamp(candles[i][0], tz=timezone.utc).strftime("%Y-%m-%d")
    trades, open_pos, _ = replay(candles, fast=fast, slow=slow, unit=unit)

    up = (s is not None) and price > s and f > s
    exit_line = s * (1 - BUFFER_PCT / 100) if s is not None else None
    return {
        "date": date, "price": price, "slow": s, "fast": f,
        "above_slow": s is not None and price > s,
        "exit_line": exit_line,
        "below_exit": exit_line is not None and price < exit_line,
        "fast_over_slow": (s is not None and f is not None and f > s),
        "uptrend_confirmed": up, "open_pos": open_pos,
        "n_closed": len(trades),
    }


# ─────────────────────────── commands ───────────────────────────────────
def cmd_backtest(weekly=False):
    tf = _tf(weekly)
    code = PAIRS[PAIR]
    candles = fetch_candles(code, tf["interval"])
    trades, open_pos, bars = replay(candles, tf["fast"], tf["slow"], tf["unit"])
    if not bars:
        print("Not enough history to warm up the moving averages."); return

    span_yrs = (candles[-1][0] - candles[0][0]) / 86400 / 365
    print(f"\n{PAIR}  —  {len(candles)} {tf['label']} bars  "
          f"(~{span_yrs:.1f} yr of REAL Kraken data)")
    print(f"Rule: long when close > {tf['slow']}{tf['unit']} MA and "
          f"{tf['fast']}{tf['unit']} MA > {tf['slow']}{tf['unit']} MA; "
          f"exit when close falls {BUFFER_PCT}% below the {tf['slow']}{tf['unit']} MA"
          + (f" or {TRAIL_STOP_PCT}% trailing stop" if TRAIL_STOP_PCT > 0 else "")
          + f". Fee {FEE_PCT_PER_SIDE}%/side.\n")

    if not trades and not open_pos:
        print("No trades — the rule never saw a confirmed uptrend in this window.")
    eq, peak, max_dd = 1.0, 1.0, 0.0
    for t in trades:
        eq *= (1 + t["net"] / 100)
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak * 100)
        print(f"  BUY  {t['entry_date']} ${t['entry_price']:>11,.2f}   "
              f"SELL {t['exit_date']} ${t['exit_price']:>11,.2f}   "
              f"gross {t['gross']:+6.1f}%  net {t['net']:+6.1f}%   ({t['exit_reason']})")
    if open_pos:
        last = bars[-1][1]
        gross = (last - open_pos["entry_price"]) / open_pos["entry_price"] * 100
        print(f"  BUY  {open_pos['entry_date']} ${open_pos['entry_price']:>11,.2f}   "
              f"STILL OPEN at ${last:,.2f}   unrealised gross {gross:+.1f}%")

    bh = (bars[-1][1] - bars[0][1]) / bars[0][1] * 100 - 2 * FEE_PCT_PER_SIDE
    strat_ret = (eq - 1) * 100
    wins = [t for t in trades if t["net"] > 0]
    print("\n  ── result over this ~{:.1f}yr window ─────────────────────".format(span_yrs))
    print(f"  Strategy (compounded, closed trades): {strat_ret:+.1f}%   "
          f"({len(trades)} trades, {len(wins)}/{len(trades) or 1} green)"
          if trades else f"  Strategy: no closed trades")
    print(f"  Buy & hold the whole window:          {bh:+.1f}%")
    verdict = ("strategy BEAT buy & hold" if strat_ret > bh
               else "buy & hold beat the strategy — the trading SUBTRACTED value here")
    print(f"  → {verdict}.")
    print(f"  Worst closed-trade equity drawdown:   -{max_dd:.1f}%   "
          f"(FLOOR — ignores adverse move WITHIN open trades; live will be deeper)")
    if weekly:
        print("\n  Honest caveat: ~14yr spanning the 2018 & 2022 bear markets —")
        print("  this is the multi-regime test. Still small n; the MA gives up")
        print("  parabolic tops in exchange for not riding crashes down.\n")
    else:
        print("\n  Honest caveat: this is ONE ~2-year regime (Kraken's public-data")
        print("  limit). Encouraging ≠ guaranteed. It only ever works if the asset")
        print("  trends up; the MA just exits faster than holding through a crash.\n")


def cmd_signal(execute: bool, usd: float):
    code = PAIRS[PAIR]
    candles = fetch_candles(code)
    v = current_view(candles)
    if v["slow"] is None:
        print("Not enough daily history yet to compute the trend."); return

    print(f"\n{PAIR}  as of {v['date']}  (last completed daily bar)")
    print(f"  close            ${v['price']:,.2f}")
    print(f"  {SLOW}-day MA        ${v['slow']:,.2f}   "
          f"(close is {'ABOVE ✅' if v['above_slow'] else 'below ❌'})")
    print(f"  {FAST}-day MA         ${v['fast']:,.2f}   "
          f"({FAST}d {'> ' if v['fast_over_slow'] else '≤ '}{SLOW}d "
          f"{'✅' if v['fast_over_slow'] else '❌'})")
    print(f"  uptrend confirmed: {'YES ✅' if v['uptrend_confirmed'] else 'NO ❌'}")
    print(f"  sell-trigger line ${v['exit_line']:,.2f}  ({BUFFER_PCT}% below the "
          f"{SLOW}d MA — close is "
          f"{'BELOW it ❌ → exit' if v['below_exit'] else 'above it ✅ → hold'})")

    state = _load_state()
    held = state.get(PAIR)
    if held:
        peak = max(held["peak"], v["price"])
        gross = (v["price"] - held["entry_price"]) / held["entry_price"] * 100
        print(f"\n  POSITION: long since {held['entry_date']} @ "
              f"${held['entry_price']:,.2f}  ({'LIVE' if held.get('live') else 'dry-run'})"
              f"  — now {gross:+.1f}% gross")
        if TRAIL_STOP_PCT > 0:
            print(f"            trailing-stop level: "
                  f"${peak * (1 - TRAIL_STOP_PCT/100):,.2f} (peak ${peak:,.2f})")
    else:
        print("\n  POSITION: flat (holding USD)")

    # Decide the action exactly as THE RULE says.
    action, why = "HOLD / do nothing", "no rule condition met"
    if held is None and v["uptrend_confirmed"]:
        action, why = "BUY", "flat + uptrend confirmed"
    elif held is not None:
        if v["below_exit"]:
            action, why = "SELL", (f"close fell {BUFFER_PCT}% below the {SLOW}d MA "
                                   f"(exit line ${v['exit_line']:,.2f}) — trend broke")
        elif TRAIL_STOP_PCT > 0 and v["price"] <= max(held['peak'], v['price']) * (1 - TRAIL_STOP_PCT/100):
            action, why = "SELL", f"trailing stop hit ({TRAIL_STOP_PCT}% off peak)"
    print(f"\n  ➜ ACTION TODAY: {action}   ({why})")

    if not execute:
        print("    (signal only — pass --execute to act on it)\n")
        return
    _execute(action, held, usd, v, state)


# ─────────────────────────── execution ──────────────────────────────────
def _load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def _save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _execute(action, held, usd, v, state):
    """Place the BUY/SELL on Kraken. DRY-RUN unless KRAKEN_ALLOW_TRADING=true
    (same double-gate the arena uses — nothing real happens by accident)."""
    if action == "HOLD / do nothing":
        print("    nothing to execute.\n"); return

    sys.path.insert(0, str(SKILL))
    from shared.kraken_executor import KrakenExecutor, KrakenExecutorError

    env_allow = os.environ.get("KRAKEN_ALLOW_TRADING", "false").lower() == "true"
    validate = not env_allow      # real order ONLY if the env gate is open
    code = PAIRS[PAIR]
    ex = KrakenExecutor()
    try:
        if action == "BUY":
            vol = usd / v["price"]
            r = ex.place_market_order(code, "buy", vol, validate=validate)
            print(f"    {'LIVE' if env_allow else 'DRY-RUN'} BUY ~{vol:.8f} "
                  f"({PAIR}, ~${usd:.2f}) → order {r.get('order_id') or 'validate-only'}")
            if env_allow:
                state[PAIR] = {"entry_date": v["date"], "entry_price": v["price"],
                               "qty": vol, "peak": v["price"], "live": True}
                _save_state(state)
        elif action == "SELL":
            if not held:
                print("    rule says SELL but no recorded position — nothing to close."); return
            if held.get("live") and not env_allow:
                print("    REFUSING: a LIVE position is open but the trading gate is "
                      "closed. Not simulating a close over real money. Open "
                      "KRAKEN_ALLOW_TRADING=true to actually exit, or flatten manually.")
                return
            r = ex.place_market_order(code, "sell", held["qty"], validate=validate)
            print(f"    {'LIVE' if env_allow else 'DRY-RUN'} SELL {held['qty']:.8f} "
                  f"({PAIR}) → order {r.get('order_id') or 'validate-only'}")
            if env_allow:
                state.pop(PAIR, None)
                _save_state(state)
    except KrakenExecutorError as e:
        print(f"    Kraken refused the order: {e}")
    print()


def cmd_sweep(weekly=False):
    """Run the EXACT same rule with the SAME locked params across every pair.
    No per-pair tuning is even possible — fast/slow/buffer/fee are identical
    for all 6. Weekly mode keeps the strategy's economic horizon constant
    (see _tf) so this is the luck-vs-structure AND multi-regime test."""
    tf = _tf(weekly)
    print(f"\n{tf['label'].upper()} — locked params, IDENTICAL for every pair "
          f"(zero tuning): FAST={tf['fast']}{tf['unit']}  "
          f"SLOW={tf['slow']}{tf['unit']}  BUFFER={BUFFER_PCT}%  "
          f"fee={FEE_PCT_PER_SIDE}%/side")
    hdr = (f"  {'pair':<9}{'bars':>6}{'yrs':>6}{'trades':>8}{'green':>7}"
           f"{'strat%':>9}{'B&H%':>9}  verdict")
    print("\n" + hdr + "\n  " + "-" * (len(hdr) - 2))
    beat = rows = 0
    for name, code in PAIRS.items():
        try:
            candles = fetch_candles(code, tf["interval"])
        except Exception as e:
            print(f"  {name:<9}  fetch failed: {e}")
            continue
        trades, open_pos, bars = replay(candles, tf["fast"], tf["slow"], tf["unit"])
        if not bars:
            print(f"  {name:<9}  not enough history")
            continue
        yrs = (candles[-1][0] - candles[0][0]) / 86400 / 365
        eq = 1.0
        for t in trades:
            eq *= (1 + t["net"] / 100)          # same formula as `backtest`
        strat = (eq - 1) * 100
        bh = (bars[-1][1] - bars[0][1]) / bars[0][1] * 100 - 2 * FEE_PCT_PER_SIDE
        green = sum(1 for t in trades if t["net"] > 0)
        won = strat > bh
        beat += int(won)
        rows += 1
        print(f"  {name:<9}{len(candles):>6}{yrs:>6.1f}{len(trades):>8}{green:>7}"
              f"{strat:>+9.1f}{bh:>+9.1f}  "
              f"{'beat B&H' if won else 'lost to B&H'}"
              f"{'  (+open leg not counted)' if open_pos else ''}")
        time.sleep(1.5)                          # be kind to Kraken's public API
    if rows:
        print(f"\n  → the locked-{BUFFER_PCT}% rule beat buy & hold in "
              f"{beat}/{rows} pairs, with NOTHING tuned per pair.")
    if weekly:
        print("\n  Honest read: ~14yr INCLUDING the 2018 & 2022 bear markets —")
        print("  this is the multi-regime test. 'beat B&H' in a crash often means")
        print("  'lost less' not 'made money' — read the strat% sign, not just the")
        print("  verdict. The MA gives up parabolic tops to dodge the crashes;")
        print("  small n per pair; $86 → cents. Method test, not income.\n")
    else:
        print("\n  Honest read: same ~2yr mostly-bull window, small n per pair,")
        print("  headline counts CLOSED trades only (open legs flagged, not scored).")
        print("  Beating B&H broadly with zero per-pair tuning ⇒ structural, not a")
        print("  BTC fluke — but NOT multi-regime proof; $86 → cents. Method test.\n")


# ─────────────────────────────── cli ────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Slow trend-participation tool.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    bt = sub.add_parser("backtest", help="replay real data, compare to buy&hold")
    bt.add_argument("--weekly", action="store_true",
                    help="~14yr weekly bars (multi-regime: 2018 & 2022 bears) instead of ~2yr daily")
    sw = sub.add_parser("sweep", help="run the locked rule across ALL pairs (luck-vs-structure test)")
    sw.add_argument("--weekly", action="store_true",
                    help="~14yr weekly bars across all pairs (the multi-regime test)")
    sp = sub.add_parser("signal", help="where we stand now + what to do, and why")
    sp.add_argument("--execute", action="store_true",
                    help="also place the order (DRY-RUN unless KRAKEN_ALLOW_TRADING=true)")
    sp.add_argument("--usd", type=float, default=25.0,
                    help="$ to spend on a BUY (default 25)")
    a = ap.parse_args()
    if a.cmd == "backtest":
        cmd_backtest(a.weekly)
    elif a.cmd == "sweep":
        cmd_sweep(a.weekly)
    else:
        cmd_signal(a.execute, a.usd)


if __name__ == "__main__":
    main()
