#!/usr/bin/env python3
"""Market-replay backtest — larger universe, many days, same JSON as the dashboard.

Replays the LIVE funnel over history instead of a hand-picked candidate list:
  1. Universe: top-N most-LIQUID US common stocks (TradingView screener, by volume).
  2. Fetch ~2000 2-min candles each (CACHED to disk; ~10 trading days/fetch).
  3. For each trading day: find that day's GAPPERS (close>=$5, gap in [min,max]%),
     classify bar-1 with the SAME classifier the live agent uses (ATR-normalized
     TIGHT + power-bar + location → MATCH), rank the MATCHes by tightness, take
     the top-N (default 5).
  4. Simulate the OpeningEngine (breakeven ratchet) on each — reuses simulate_trade.
  5. Aggregate → JSON for trial-results.template.html.

HONEST CONSTRAINTS (shown on the dashboard subtitle):
  - "Whole market" isn't fetchable via per-symbol Questrade calls. We use a broad
    LIQUID universe (UNIVERSE_N, default 150). Recency/survivorship bias: it's
    today's liquid names replayed backward.
  - Lookback is bounded by one candle fetch (~2000 2-min bars ≈ 10 trading days).
    Set TRIAL_CANDLE_COUNT higher for more, at the cost of fetch time.
  - Pre-market relative-volume (the live screener's sort) isn't reconstructable
    from RTH candles, so daily selection uses gap-filter + the classifier's
    MATCH/tightness — the strategy's primary selector anyway.

  python3 trial_backtest_market.py > /tmp/trial_market.json
  python3 trial-results_update.py /tmp/trial_market.json
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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
from shared.questrade_executor import QuestradeExecutor
from shared import indicators
from opening_agent import classifier as C
# Reuse the proven single-trade simulator + its constants/fetch from trial_backtest.
from trial_backtest import simulate_trade, fetch_candles, ET, POS_USD, START_CAPITAL, SLIP, CUTOFF_MIN

UNIVERSE_N   = int(os.environ.get("TRIAL_UNIVERSE_N", "150"))
TOP_N        = int(os.environ.get("TRIAL_TOP_N", "5"))
CANDLE_COUNT = int(os.environ.get("TRIAL_CANDLE_COUNT", "2000"))
MIN_GAP      = float(os.environ.get("OPENING_SCAN_MIN_GAP_PCT", "1"))
MAX_GAP      = float(os.environ.get("OPENING_SCAN_MAX_GAP_PCT", "6"))
MIN_PRICE    = float(os.environ.get("OPENING_MIN_PRICE", "5"))
OPEN_T       = time(9, 30)
CACHE_DIR    = Path(os.environ.get("TRIAL_CACHE_DIR", "/tmp/trial_candle_cache"))
_CFG         = dict(C.DEFAULTS)


def get_universe(n):
    """Top-n most IN-PLAY US common stocks by RELATIVE volume (rising participation
    vs the stock's own 10d norm) — the gappy small/mid-caps the strategy actually
    trades, NOT the highest-absolute-volume mega-caps (which never gap). Mirrors
    the live screener's sort; the per-day replay then applies the gap filter."""
    body = {
        "filter": [
            {"left": "exchange", "operation": "in_range", "right": ["AMEX", "NASDAQ", "NYSE"]},
            {"left": "type", "operation": "in_range", "right": ["stock"]},
            {"left": "close", "operation": "in_range", "right": [MIN_PRICE, 500]},   # exclude penny + ultra-high px
            {"left": "volume", "operation": "greater", "right": 2000000},            # real liquidity floor (drops junk spikes)
        ],
        "options": {"lang": "en"},
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": ["name", "close", "volume", "relative_volume_10d_calc"],
        "sort": {"sortBy": "relative_volume_10d_calc", "sortOrder": "desc"},
        "range": [0, int(n)],
    }
    req = urllib.request.Request(
        "https://scanner.tradingview.com/america/scan",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/145.0.0.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = json.loads(r.read().decode()).get("data", [])
    out = []
    for row in rows:
        sym = row["s"].split(":")[-1]               # "NASDAQ:AAPL" -> "AAPL"
        out.append(sym)
    return out


CACHED_ONLY = os.environ.get("TRIAL_CACHED_ONLY", "0") == "1"


def candles_cached(symbol):
    """Fetch (and disk-cache) 2-min candles for a symbol. With TRIAL_CACHED_ONLY=1,
    return None for any symbol not already cached (no network) — for a fast replay
    on whatever's been fetched so far."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fp = CACHE_DIR / f"{symbol}.json"
    if fp.exists():
        try:
            raw = json.loads(fp.read_text())
            return [{**b, "et": datetime.fromisoformat(b["et"])} for b in raw]
        except Exception:
            pass
    if CACHED_ONLY:
        return None
    bars = fetch_candles(symbol, count=CANDLE_COUNT)
    try:
        fp.write_text(json.dumps([{**b, "et": b["et"].isoformat()} for b in bars]))
    except Exception:
        pass
    return bars


def trading_dates(all_bars):
    """Distinct dates that have a >=9:30 bar, oldest→newest (skip the first date —
    it has no prior session for the gap / not enough SMA200 warmup)."""
    dates = sorted({b["et"].date() for b in all_bars})
    return dates[1:] if len(dates) > 1 else []


def day_setup(symbol, all_bars, target_date):
    """Classify symbol's bar-1 on target_date with the LIVE classifier.
    Returns dict {tightness, state, decision, gap, price} for a TIGHT MATCH_LONG,
    else None. Long-only (the account can't short)."""
    upto = [b for b in all_bars if b["et"].date() <= target_date]
    day_bars = [b for b in upto if b["et"].date() == target_date]
    if len(upto) < 210 or not day_bars:
        return None
    # bar-1 = first bar at/after 9:30 ET
    bar1 = next((b for b in day_bars if b["et"].time() >= OPEN_T), None)
    if bar1 is None:
        return None
    # prior session close = last bar strictly before target_date
    prior = [b for b in upto if b["et"].date() < target_date]
    if not prior:
        return None
    prior_close = prior[-1]["close"]
    if prior_close <= 0 or bar1["open"] < MIN_PRICE:
        return None
    gap = (bar1["open"] - prior_close) / prior_close * 100
    if not (MIN_GAP <= abs(gap) <= MAX_GAP):
        return None
    # SMAs + classifier verdict on bars UP TO bar-1 (inclusive)
    idx = upto.index(bar1)
    series = upto[:idx + 1]
    closes = [b["close"] for b in series]
    smf, sms = indicators.sma(closes, 20), indicators.sma(closes, 200)
    if smf is None or sms is None:
        return None
    v = C.classify_opening(symbol, bar1, series[:-1], smf, sms, _CFG)
    if v.decision != "MATCH_LONG":
        return None
    atr_val = C.atr(series, _CFG["atr_len"])
    return {
        "tightness": C.tightness(smf, sms, bar1["open"], _CFG, atr_val=atr_val) or 0,
        "state": v.state, "decision": v.decision,
        "gap": round(gap, 2), "price": round(bar1["open"], 2),
    }


def main():
    print(f"[market] universe top-{UNIVERSE_N} liquid US stocks...", file=sys.stderr)
    universe = get_universe(UNIVERSE_N)
    print(f"[market] {len(universe)} symbols; fetching candles (cached in {CACHE_DIR})...",
          file=sys.stderr)

    # 1) load candles for the whole universe (cached)
    bars_by_sym = {}
    for i, sym in enumerate(universe, 1):
        try:
            b = candles_cached(sym)
            if b:
                bars_by_sym[sym] = b
        except Exception as e:
            print(f"  [{sym}] fetch error: {e}", file=sys.stderr)
        if i % 25 == 0:
            print(f"  ...{i}/{len(universe)} fetched", file=sys.stderr)

    # 2) the set of trading dates across the universe
    all_dates = sorted({d for b in bars_by_sym.values() for d in trading_dates(b)})
    print(f"[market] replaying {len(all_dates)} trading days over {len(bars_by_sym)} symbols",
          file=sys.stderr)

    all_trades, daily_summary = [], []
    for date in all_dates:
        # find that day's TIGHT MATCH_LONG qualifiers across the universe
        quals = []
        for sym, bars in bars_by_sym.items():
            try:
                setup = day_setup(sym, bars, date)
            except Exception:
                setup = None
            if setup:
                quals.append((sym, setup))
        quals.sort(key=lambda x: -x[1]["tightness"])
        top = quals[:TOP_N]

        day_trades = []
        for sym, setup in top:
            try:
                r = simulate_trade(sym, bars_by_sym[sym], date)
                r.update({"scan_tightness": round(setup["tightness"], 3),
                          "scan_state": setup["state"], "scan_gap": f"{setup['gap']:+.1f}%",
                          "scan_score": round(setup["tightness"] * 100, 1)})
                day_trades.append(r)
            except Exception as e:
                day_trades.append({"symbol": sym, "date": str(date), "status": "error", "error": str(e)})

        traded = [t for t in day_trades if t.get("status") == "traded"]
        daily_summary.append({
            "date": str(date), "candidates": len(quals), "traded": len(traded),
            "not_triggered": sum(1 for t in day_trades if t.get("status") == "not_triggered"),
            "day_pnl": round(sum(t.get("pnl", 0) for t in traded), 2),
            "trades": day_trades,
        })
        all_trades.extend(day_trades)
        print(f"  [{date}] {len(quals)} qualifiers, traded {len(traded)}, "
              f"day P&L ${sum(t.get('pnl',0) for t in traded):+.2f}", file=sys.stderr)

    # 3) stats + equity curve (same shape as trial_backtest.py)
    traded = [t for t in all_trades if t.get("status") == "traded"]
    wins = [t for t in traded if t["pnl"] > 0]
    losses = [t for t in traded if t["pnl"] <= 0]
    total_pnl = sum(t["pnl"] for t in traded)
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    equity = START_CAPITAL
    equity_curve = [{"date": "start", "equity": equity, "label": "Starting capital"}]
    for t in sorted(traded, key=lambda x: (x["date"], x["symbol"])):
        equity += t["pnl"]
        equity_curve.append({"date": t["date"], "symbol": t["symbol"],
                             "pnl": t["pnl"], "equity": round(equity, 2)})

    output = {
        "title": "Opening Power — Market-Replay Backtest",
        "subtitle": (f"Top-{UNIVERSE_N} liquid US stocks · {len(all_dates)} trading days · "
                     f"gap {MIN_GAP}-{MAX_GAP}% + ATR-TIGHT MATCH, top-{TOP_N}/day · "
                     f"breakeven engine · real Questrade candles"),
        "updated": datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"),
        "config": {"start_capital": START_CAPITAL, "position_usd": POS_USD,
                   "slippage_pct": SLIP, "cutoff_min": CUTOFF_MIN,
                   "max_trades_per_day": TOP_N, "universe_n": UNIVERSE_N,
                   "trading_days": len(all_dates)},
        "stats": {
            "total_trades": len(traded),
            "total_signals": sum(1 for t in all_trades if t.get("status") in ("traded", "not_triggered")),
            "wins": len(wins), "losses": len(losses),
            "win_rate": round(100 * len(wins) / len(traded), 1) if traded else 0,
            "net_pnl": round(total_pnl, 2),
            "net_pct": round(total_pnl / START_CAPITAL * 100, 2),
            "final_equity": round(equity, 2),
            "avg_win": round(gross_win / len(wins), 2) if wins else 0,
            "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0,
            "avg_win_pct": round(sum(t["pct"] for t in wins) / len(wins), 2) if wins else 0,
            "avg_loss_pct": round(sum(t["pct"] for t in losses) / len(losses), 2) if losses else 0,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else "inf",
            "gross_win": round(gross_win, 2), "gross_loss": round(gross_loss, 2),
        },
        "equity_curve": equity_curve,
        "daily": daily_summary,
        "trades": sorted(traded, key=lambda x: (x["date"], x["symbol"])),
        "all_signals": sorted(all_trades, key=lambda x: (x["date"], -x.get("scan_score", 0))),
    }
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
