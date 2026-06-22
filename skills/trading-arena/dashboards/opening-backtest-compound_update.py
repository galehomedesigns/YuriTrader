#!/usr/bin/env python3
"""Regenerate canvas/opening-backtest-compound.html from logs/opening_backtest_summary.json.

Compounding view of the Opening-Power gap strategy over the most recent 4 weeks:
start with the live budget ($OPENING_TRADE_BUDGET_USD) and reinvest each day's
profit into the next day's trading, so daily returns compound.

SIZING MODEL A (live-faithful, chosen 2026-06-21): each day the budget = the
current balance, split into OPENING_MAX_TRADES fixed slots of balance/max_trades;
one slot per matched trade (<=max_trades), and any unused slots sit in cash (earn
0% that day). So the day's return on the balance = sum(trade %)/max_trades. This
mirrors the live auto-stage (fixed slot = budget/max_trades), NOT full deployment.

Pure data per docs/DASHBOARDS.md: read the existing backtest summary -> compute
the compounding curve -> substitute into the locked template. No re-run, no LLM,
read-only on the summary (written by opening_agent/backtest_full.py). Uses the
flatten exit % (flatten_pct) because flatten is the live OPENING_EOD_MODE.
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone, date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "opening-backtest-compound.template.html"
OUT = HERE.parents[2] / "canvas" / "opening-backtest-compound.html"   # ~/openclaw/canvas/
SUMMARY = HERE.parent / "logs" / "opening_backtest_summary.json"      # trading-arena/logs/

WEEKS = int(os.environ.get("OPENING_COMPOUND_WEEKS", "4"))
BUDGET = float(os.environ.get("OPENING_TRADE_BUDGET_USD", "1000"))
MAX_TRADES = int(os.environ.get("OPENING_MAX_TRADES", "5"))


def _d(s: str) -> date:
    y, m, d = (int(x) for x in s.split("-"))
    return date(y, m, d)


def _empty(reason: str) -> dict:
    return {
        "updated": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z"),
        "config": {"budget": BUDGET, "max_trades": MAX_TRADES, "weeks": WEEKS},
        "window": {}, "summary": {}, "series": [], "empty_reason": reason,
    }


def build_data() -> dict:
    if not SUMMARY.exists():
        return _empty("no backtest summary yet")
    try:
        summ = json.loads(SUMMARY.read_text())
    except ValueError:
        return _empty("backtest summary unreadable")

    # flatten_pct is the per-trade % return under the live EOD mode (flatten).
    trades = [t for t in summ.get("trades", []) if t.get("flatten_pct") is not None]
    if not trades:
        return _empty("no flatten trades in the summary")

    last = max(_d(t["date"]) for t in trades)
    start = last - timedelta(days=WEEKS * 7 - 1)
    win = [t for t in trades if start <= _d(t["date"]) <= last]
    by_day: dict[str, list[float]] = {}
    for t in win:
        by_day.setdefault(t["date"], []).append(float(t["flatten_pct"]))
    days = sorted(by_day)
    if not days:
        return _empty("no trades in the last %d weeks" % WEEKS)

    # ── Compound: balance grows by sum(trade%)/max_trades each day (model A) ──
    bal = BUDGET
    peak = BUDGET
    max_dd = 0.0
    win_days = 0
    series = []
    additive_ret_sum = 0.0          # linear (non-compounding) sum of daily returns
    for dy in days:
        rets = by_day[dy]
        day_ret = sum(rets) / MAX_TRADES        # idle slots earn 0%
        additive_ret_sum += day_ret
        bal *= (1 + day_ret / 100.0)
        if day_ret > 0:
            win_days += 1
        peak = max(peak, bal)
        dd = (peak - bal) / peak * 100.0
        max_dd = max(max_dd, dd)
        series.append({
            "date": dy, "n": len(rets),
            "day_ret_pct": round(day_ret, 3),
            "balance": round(bal, 2),
            "deployed_pct": round(100.0 * len(rets) / MAX_TRADES, 1),
        })

    best = max(series, key=lambda s: s["day_ret_pct"])
    worst = min(series, key=lambda s: s["day_ret_pct"])
    end_bal = round(bal, 2)
    additive_end = round(BUDGET * (1 + additive_ret_sum / 100.0), 2)

    return {
        "updated": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z"),
        "config": {
            "budget": BUDGET, "max_trades": MAX_TRADES, "weeks": WEEKS,
            "eod_mode": "flatten", "selection": summ.get("config", {}).get("selection"),
            "slippage_pct": summ.get("config", {}).get("slippage_pct"),
        },
        "window": {"start": days[0], "end": days[-1], "trading_days": len(days)},
        "summary": {
            "start_balance": BUDGET,
            "end_balance": end_bal,
            "total_return_pct": round((end_bal - BUDGET) / BUDGET * 100.0, 2),
            "win_days": win_days, "total_days": len(days),
            "best_day": {"date": best["date"], "ret": best["day_ret_pct"]},
            "worst_day": {"date": worst["date"], "ret": worst["day_ret_pct"]},
            "max_drawdown_pct": round(max_dd, 2),
            "additive_end": additive_end,
            "compounding_bonus": round(end_bal - additive_end, 2),
        },
        "series": series,
    }


def main() -> None:
    html = TEMPLATE.read_text().replace("{{DATA}}", json.dumps(build_data(), default=str))
    OUT.write_text(html)
    print(f"[opening-backtest-compound] wrote {OUT}")


if __name__ == "__main__":
    main()
