#!/usr/bin/env python3
"""5-Day Trial Backtest — proves opening strategy profitability using real data.

Extracts the top TIGHT candidates from each day's pre-market scan, fetches real
Questrade 2-min candles, and simulates bar-1 breakout trades:
  entry = bar-1 high (buy-stop), stop = bar-1 low, cutoff = 20 min.

Assumptions:
  - $1,000 starting capital, $200 per trade (max 5 trades/day)
  - 0.10% slippage on entry + exit, $0 commission (Questrade)
  - Long-only, whole shares
  - Only TIGHT stocks from the 09:25 ET scan are eligible

Outputs JSON to stdout (pipe into the dashboard template).
"""
import json
import os
import sys
from datetime import datetime, time, timedelta
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

ET = ZoneInfo("America/New_York")
SLIP = 0.0010
POS_USD = 200.0
START_CAPITAL = 1000.0
OPEN_T = time(9, 30)
CUTOFF_MIN = 30
MAX_RISK_PCT = 3.0     # bar-1 risk cap (only applied if micro-filters on)
MIN_BAR_RANGE = 0.05   # bar-1 min range (only applied if micro-filters on)
# We trade the N BEST-scoring qualifiers that meet the TIGHT (ATR-normalized) criteria
# each day. The secondary risk/range micro-filters are OFF by default so the "5 best"
# aren't pre-trimmed — set TRIAL_MICRO_FILTERS=1 to re-enable them for comparison.
SELECT_TOP_N = int(os.environ.get("TRIAL_TOP_N", "5"))
APPLY_MICRO_FILTERS = os.environ.get("TRIAL_MICRO_FILTERS", "0") == "1"

# Top TIGHT candidates from each day's 09:25 ET scan (extracted from scan_cron.log).
# Format: {date: [(symbol, score, tightness, state, gap%), ...]}
DAILY_CANDIDATES = {
    "2026-06-12": [
        ("AMD", 50.2, 0.299, "TIGHT/above", "+0.8%"),
    ],
    "2026-06-15": [
        # No TIGHT stocks in the scan — all WIDE. No trades.
    ],
    "2026-06-16": [
        ("HL",   87.6, 0.905, "TIGHT/above",  "+1.3%"),
        ("CCL",  60.3, 0.566, "TIGHT/below",  "+1.0%"),
        ("CDE",  40.8, 0.054, "TIGHT/inside", "+1.2%"),
        ("AG",   37.0, 0.007, "TIGHT/inside", "+1.4%"),
    ],
    "2026-06-17": [
        ("KEEL", 79.8, 0.912, "TIGHT/below", "+1.3%"),
        ("BTQ",  61.6, 0.983, "TIGHT/below", "+3.8%"),
        ("ABCL", 29.7, 0.452, "TIGHT/below", "+2.2%"),
        ("MP",   32.4, 0.641, "TIGHT/below", "+1.3%"),
        ("EOSE", 32.1, 0.365, "TIGHT/below", "+5.4%"),
    ],
    "2026-06-18": [
        ("HIMS", 78.3, 0.597, "TIGHT/below",  "+1.8%"),
        ("USAR", 36.3, 0.988, "TIGHT/below",  "+3.8%"),
        ("MNTS", 35.0, 0.946, "TIGHT/above",  "+3.1%"),
        ("HOOD", 30.9, 0.604, "TIGHT/below",  "+2.0%"),
        ("NBIS", 30.4, 0.434, "TIGHT/below",  "+5.2%"),
    ],
}


def fetch_candles(symbol, count=2000):
    """Fetch timestamped 2-min candles via Questrade (read-only)."""
    q = QuestradeExecutor()
    raw = q.get_candles(symbol, interval="TwoMinutes", count=count)
    out = []
    for c in raw or []:
        try:
            t = c.get("end") or c.get("start")
            dt = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(ET)
            out.append({
                "et": dt, "open": float(c["open"]), "high": float(c["high"]),
                "low": float(c["low"]), "close": float(c["close"]),
                "volume": float(c.get("volume", 0) or 0),
            })
        except Exception:
            continue
    return out


def simulate_trade(symbol, all_bars, target_date):
    """Simulate one bar-1 breakout trade on target_date using the full
    OpeningEngine (push/pause ratchets, counter-bar adds, breakeven stop).

    Returns dict with trade details, or None if not enough data.
    """
    from opening_agent.engine import OpeningEngine, ARMED, IN_HALF, IN_FULL, FLAT
    from opening_agent import classifier as C

    day_bars = [b for b in all_bars if b["et"].date() == target_date]

    # Find bar-1 = first bar at or after 9:30 ET
    oi = None
    for i, b in enumerate(day_bars):
        if b["et"].time() >= OPEN_T:
            oi = i
            break
    if oi is None:
        return {"symbol": symbol, "date": str(target_date), "status": "no_open_bar"}

    bar1 = day_bars[oi]
    entry_level = bar1["high"] + C.DEFAULTS["trade_offset"]
    stop_level = bar1["low"] - C.DEFAULTS["trade_offset"]

    if entry_level <= stop_level:
        return {"symbol": symbol, "date": str(target_date), "status": "invalid_bar",
                "bar1_high": round(bar1["high"], 4), "bar1_low": round(bar1["low"], 4)}

    bar_spread = entry_level - stop_level

    risk_pct = bar_spread / entry_level * 100
    # Secondary micro-filters (OFF by default — we trade the 5 best that meet the
    # TIGHT criteria; enable with TRIAL_MICRO_FILTERS=1 to compare).
    if APPLY_MICRO_FILTERS:
        if bar_spread < MIN_BAR_RANGE:
            return {"symbol": symbol, "date": str(target_date), "status": "range_too_narrow",
                    "bar1_high": round(bar1["high"], 4), "bar1_low": round(bar1["low"], 4),
                    "bar_range": round(bar_spread, 4)}
        if risk_pct > MAX_RISK_PCT:
            return {"symbol": symbol, "date": str(target_date), "status": "risk_too_high",
                    "bar1_high": round(bar1["high"], 4), "bar1_low": round(bar1["low"], 4),
                    "risk_pct": round(risk_pct, 2)}

    # Position sizing — full qty for the $200 slot
    entry_fill_est = entry_level * (1 + SLIP)
    full_qty = int(POS_USD // entry_fill_est)
    if full_qty < 1:
        return {"symbol": symbol, "date": str(target_date), "status": "too_expensive",
                "price": round(entry_level, 2)}

    # Set up the engine manually (bypass classifier — we already filtered TIGHT)
    eng = OpeningEngine(symbol)
    eng.bar1 = bar1
    eng.side = 1                     # long only
    eng.state = ARMED
    eng.entry_price = entry_level
    eng.stop_price = stop_level
    eng.shares = full_qty            # full target (engine enters half, adds half)
    eng.push = None                  # set on entry

    # Cutoff timestamp
    open_ts = datetime.combine(target_date, OPEN_T, ET).timestamp()
    cutoff_ts = open_ts + CUTOFF_MIN * 60

    # Follow bars within the cutoff window
    follow = [b for b in day_bars[oi:] if b["et"].timestamp() <= cutoff_ts + 1]

    # Feed bars through the engine, tracking all order tickets
    all_tickets = []
    high_after_entry = None
    entry_fill = None
    exit_fill = None
    reason = None
    total_bought = 0
    total_sold = 0
    add_count = 0
    trigger_bar_idx = None        # which follow-bar (1-based) the entry filled on

    for idx, b in enumerate(follow[1:], 1):  # skip bar1 itself
        tickets = eng.on_bar(b, complete=True)
        all_tickets.extend(tickets)

        for t in tickets:
            if t.side == "BUY" and t.order_type in ("STP", "MKT"):
                # Entry or add fill
                fill_price = t.price * (1 + SLIP) if t.order_type == "STP" else b["close"] * (1 + SLIP)
                if entry_fill is None:
                    entry_fill = fill_price
                    trigger_bar_idx = idx
                else:
                    add_count += 1
                total_bought += t.qty
            elif t.side == "SELL" and t.order_type == "MKT":
                # Stop hit or cutoff flatten
                exit_fill = t.price * (1 - SLIP) if t.price > 0 else b["close"] * (1 - SLIP)
                total_sold += t.qty
                if "stop hit" in t.reason:
                    reason = "stop"
                elif "cutoff" in t.reason:
                    reason = "cutoff"
                elif "breakeven" in t.reason.lower():
                    reason = "breakeven"

        if entry_fill is not None:
            high_after_entry = max(high_after_entry or 0, b["high"])

        if eng.state == FLAT:
            break

    # Cutoff: if still in trade at window end
    if eng.state in (IN_HALF, IN_FULL) and eng.filled > 0:
        cutoff_tickets = eng.on_cutoff()
        all_tickets.extend(cutoff_tickets)
        for t in cutoff_tickets:
            if t.side == "SELL":
                exit_fill = follow[-1]["close"] * (1 - SLIP)
                total_sold += t.qty
                reason = "cutoff"

    if entry_fill is None:
        return {
            "symbol": symbol, "date": str(target_date), "status": "not_triggered",
            "entry_level": round(entry_level, 4), "stop_level": round(stop_level, 4),
            "bar1_high": round(bar1["high"], 4), "bar1_low": round(bar1["low"], 4),
            "bar1_open": round(bar1["open"], 4), "bar1_close": round(bar1["close"], 4),
            "day_high": round(max(b["high"] for b in follow), 4),
        }

    if exit_fill is None:
        exit_fill = follow[-1]["close"] * (1 - SLIP)
        reason = "cutoff"

    # P&L: compute from all fills. Simplified: avg entry on all buys vs exit on sells.
    # With slippage already applied above, use the tracked entry_fill as avg entry.
    final_qty = total_bought  # all shares that entered (half + adds)
    pnl = (exit_fill - entry_fill) * final_qty
    pct = (exit_fill - entry_fill) / entry_fill * 100

    # Journal summary
    journal_events = []
    for t in all_tickets:
        journal_events.append(f"{t.side} {t.qty}sh {t.order_type} @{t.price:.2f} ({t.reason})")

    return {
        "symbol": symbol, "date": str(target_date), "status": "traded",
        "triggered": True, "reason": reason,
        "entry": round(entry_fill, 4), "exit": round(exit_fill, 4),
        "stop_level": round(stop_level, 4),
        "qty": final_qty, "pct": round(pct, 3), "pnl": round(pnl, 2),
        "half_entry": max(1, int(full_qty * 0.5)),
        "adds": add_count,
        "pushes": eng.push.pushes if eng.push else 0,
        "final_stop": round(eng.stop_price, 4) if eng.stop_price else None,
        "bar1_high": round(bar1["high"], 4), "bar1_low": round(bar1["low"], 4),
        "bar1_open": round(bar1["open"], 4), "bar1_close": round(bar1["close"], 4),
        "high_after_entry": round(high_after_entry, 4) if high_after_entry else None,
        "max_unrealized_pct": round((high_after_entry - entry_fill) / entry_fill * 100, 2) if high_after_entry else None,
        "journal": journal_events,
        "trigger_bar": trigger_bar_idx,
    }


def main():
    all_trades = []
    daily_summary = []

    for date_str in sorted(DAILY_CANDIDATES.keys()):
        # The N best-scoring qualifiers that met the TIGHT criteria that day.
        candidates = sorted(DAILY_CANDIDATES[date_str], key=lambda c: -c[1])[:SELECT_TOP_N]
        target = datetime.strptime(date_str, "%Y-%m-%d").date()
        day_trades = []

        if not candidates:
            daily_summary.append({
                "date": date_str, "candidates": 0, "traded": 0,
                "day_pnl": 0, "trades": [],
            })
            continue

        print(f"[{date_str}] processing {len(candidates)} TIGHT candidates...",
              file=sys.stderr)

        for sym, score, tightness, state, gap in candidates:
            print(f"  {sym} (score {score}, tight {tightness})...",
                  end="", file=sys.stderr)
            try:
                bars = fetch_candles(sym, count=2000)
                result = simulate_trade(sym, bars, target)
                result["scan_score"] = score
                result["scan_tightness"] = tightness
                result["scan_state"] = state
                result["scan_gap"] = gap
                day_trades.append(result)
                status = result.get("status", "?")
                if status == "traded":
                    pnl_str = f"${result['pnl']:+.2f} ({result['pct']:+.2f}%)"
                    print(f" {result['reason']} {pnl_str}", file=sys.stderr)
                else:
                    print(f" {status}", file=sys.stderr)
            except Exception as e:
                print(f" ERROR: {e}", file=sys.stderr)
                day_trades.append({
                    "symbol": sym, "date": date_str, "status": "error",
                    "error": str(e),
                })

        traded = [t for t in day_trades if t.get("status") == "traded"]
        day_pnl = sum(t.get("pnl", 0) for t in traded)
        daily_summary.append({
            "date": date_str,
            "candidates": len(candidates),
            "traded": len(traded),
            "not_triggered": sum(1 for t in day_trades if t.get("status") == "not_triggered"),
            "day_pnl": round(day_pnl, 2),
            "trades": day_trades,
        })
        all_trades.extend(day_trades)

    # Compute equity curve
    traded = [t for t in all_trades if t.get("status") == "traded"]
    equity = START_CAPITAL
    equity_curve = [{"date": "start", "equity": equity, "label": "Starting capital"}]
    for t in sorted(traded, key=lambda x: (x["date"], x["symbol"])):
        equity += t["pnl"]
        equity_curve.append({
            "date": t["date"], "symbol": t["symbol"],
            "pnl": t["pnl"], "equity": round(equity, 2),
        })

    # Stats
    wins = [t for t in traded if t["pnl"] > 0]
    losses = [t for t in traded if t["pnl"] <= 0]
    total_pnl = sum(t["pnl"] for t in traded)
    win_rate = (100 * len(wins) / len(traded)) if traded else 0
    avg_win = (sum(t["pnl"] for t in wins) / len(wins)) if wins else 0
    avg_loss = (sum(t["pnl"] for t in losses) / len(losses)) if losses else 0
    avg_win_pct = (sum(t["pct"] for t in wins) / len(wins)) if wins else 0
    avg_loss_pct = (sum(t["pct"] for t in losses) / len(losses)) if losses else 0
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")

    output = {
        "title": "Opening Power — 5-Day Trial",
        "subtitle": "Real Questrade candle data, bar-1 breakout on top TIGHT signals",
        "updated": datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"),
        "config": {
            "start_capital": START_CAPITAL,
            "position_usd": POS_USD,
            "slippage_pct": SLIP,
            "cutoff_min": CUTOFF_MIN,
            "max_trades_per_day": 5,
        },
        "stats": {
            "total_trades": len(traded),
            "total_signals": sum(1 for t in all_trades if t.get("status") in ("traded", "not_triggered")),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 1),
            "net_pnl": round(total_pnl, 2),
            "net_pct": round(total_pnl / START_CAPITAL * 100, 2),
            "final_equity": round(equity, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "avg_win_pct": round(avg_win_pct, 2),
            "avg_loss_pct": round(avg_loss_pct, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf",
            "gross_win": round(gross_win, 2),
            "gross_loss": round(gross_loss, 2),
        },
        "equity_curve": equity_curve,
        "daily": daily_summary,
        "trades": sorted(traded, key=lambda x: (x["date"], x["symbol"])),
        "all_signals": sorted(all_trades, key=lambda x: (x["date"], -x.get("scan_score", 0))),
    }
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
