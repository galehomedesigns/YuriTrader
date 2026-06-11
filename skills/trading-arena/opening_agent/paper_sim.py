#!/usr/bin/env python3
"""Paper-trade SIMULATION — drive the execution engine through a full opening and
push the resulting trade (every rule firing) to YuriStocks Telegram.

This is a SIMULATION on representative 2-min bars, not a live broker fill. It
exists to demonstrate the R2-R7 rules end-to-end and to produce a Telegram
artifact you can inspect. P&L is computed from the engine's OWN emitted tickets
(entry/add fills) + the exit price — so it proves the rules, not a hand-wave.

A true live paper trade additionally needs: market open + a real MATCH first bar
+ engine wired to the IBKR paper executor (auto_execute) + gates open. Not that.

    .venv/bin/python skills/trading-arena/opening_agent/paper_sim.py          # send
    flags: --no-send (print only)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from opening_agent.engine import OpeningEngine
from opening_agent.run_opening_scan import send_message


def bar(o, h, l, c, v=50000):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def representative_long_open():
    """A clean bull-elephant opening that breaks, pulls back once (the add), and
    pushes to the cutoff. Tight 20/200 band just under the open."""
    prior = [bar(50.00, 50.05, 49.96, 50.01) for _ in range(204)]   # body ~0.01, tight
    bar1 = bar(50.10, 50.55, 50.08, 50.50)                          # bull elephant, opens above
    sma_fast, sma_slow = 50.02, 50.00                              # TIGHT, open 50.10 above
    follow = [
        bar(50.50, 50.70, 50.48, 50.65),   # trigger (trades > 50.55+.01)
        bar(50.65, 50.66, 50.55, 50.60),   # pause
        bar(50.60, 50.90, 50.58, 50.86),   # push 1
        bar(50.88, 50.89, 50.80, 50.82),   # red counter-bar (the add candidate)
        bar(50.82, 51.05, 50.81, 51.00),   # green removes it -> ADD
        bar(51.00, 51.06, 50.95, 51.01),   # pause
        bar(51.01, 51.40, 50.99, 51.36),   # push 2 -> ratchet + rest order
        bar(51.36, 51.80, 51.34, 51.74),   # push 3
    ]
    return prior, bar1, sma_fast, sma_slow, follow


def run_sim():
    prior, bar1, smf, sms, follow = representative_long_open()
    eng = OpeningEngine("DEMO", account_equity=50000.0, cfg={"risk_per_trade": 0.01})
    tickets = list(eng.on_bar1(bar1, prior, smf, sms))
    for b in follow:
        tickets += eng.on_bar(b, complete=True)
    exit_price = follow[-1]["close"]
    tickets += eng.on_cutoff()

    # Derive fills from the engine's OWN tickets (entries/adds = BUY for a long).
    fills = [(t.qty, t.price) for t in tickets
             if t.side == "BUY" and t.order_type in ("STP", "MKT") and t.price > 0]
    shares = sum(q for q, _ in fills)
    cost = sum(q * p for q, p in fills)
    avg_entry = cost / shares if shares else 0.0
    pnl = (exit_price - avg_entry) * shares
    return eng, tickets, shares, avg_entry, exit_price, pnl


def format_msg(eng, tickets, shares, avg_entry, exit_price, pnl):
    pct = (pnl / 50000.0) * 100
    lines = [
        "🧪 <b>Opening Power — PAPER SIMULATION</b>",
        "<i>Representative 2-min opening, not a live fill. Demonstrates the rules.</i>",
        "",
        "<b>Setup:</b> DEMO — TIGHT state, bull elephant opening ABOVE the band → MATCH (long)",
        "",
        "<b>Rules fired (engine tickets):</b>",
    ]
    for t in tickets:
        px = f"@{t.price:.2f}" if t.price > 0 else "@mkt"
        lines.append(f"  • <b>{t.rule}</b> — {t.side} {t.qty} {t.order_type} {px}  <i>{t.reason}</i>")
    lines += [
        "",
        f"<b>Simulated result</b> (equity $50,000, 1% risk):",
        f"  • Shares: {shares}  avg entry ${avg_entry:.2f}  exit ${exit_price:.2f}",
        f"  • P&L: <b>${pnl:,.0f}</b> ({pct:+.2f}% of account)",
        "",
        "<i>signal_only — no broker order was placed. Gates remain OFF.</i>",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-send", action="store_true")
    a = ap.parse_args()
    res = run_sim()
    msg = format_msg(*res)
    print(msg)
    if not a.no_send:
        ok = send_message(msg)
        print(f"\n[paper_sim] telegram sent={ok}")


if __name__ == "__main__":
    main()
