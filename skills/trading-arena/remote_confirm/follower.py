#!/usr/bin/env python3
"""follower.py — per-user consumer of the broadcast signal (Phase 0 pilot, DRY).

Reads the producer's broadcast for a date, takes the first max_trades by arm order
(mirrors Tony's first-to-arm cap), sizes each to THIS user's budget using the exact
advisory_monitor sizing math, and writes per-order "pending confirm" records into
state/remote_confirm/<user>/pending/ for family_bot.py to Approve/Skip.

DRY ONLY: never sends an order. The bid is logged to Mongo on Approve (by the bot),
not here. Re-runs are idempotent (order_id = "<user>:<broadcast_id>").

Usage: follower.py --user pilot --date 2026-06-24 [--users <users.json>] [--broadcast-dir <dir>]
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.normpath(os.path.join(HERE, "..", "..", "..", "state"))
DEFAULT_BCAST = os.path.join(STATE, "remote_confirm", "broadcast")
DEFAULT_USERS = os.path.join(HERE, "users.json")

MAX_RISK_PCT = float(os.environ.get("OPENING_MAX_RISK_PCT", "3.0"))
MIN_BAR_RANGE = float(os.environ.get("OPENING_MIN_BAR_RANGE", "0.05"))


def load_user(users_path, user):
    cfg = json.load(open(users_path))
    for u in (cfg if isinstance(cfg, list) else cfg.get("users", [])):
        if u.get("user") == user:
            return u
    raise SystemExit(f"[follower] user '{user}' not in {users_path}")


def size_order(sgn, user, ucfg):
    """Replicate advisory_monitor._stage_entries sizing for one signal. Returns an
    order dict or (None, reason) if priced out / risk-capped."""
    budget = float(ucfg.get("day_budget", 1000))
    max_trades = int(ucfg.get("max_trades", 5))
    per = budget / max_trades                       # fixed per-trade slot
    entry, stop = sgn["entry"], sgn["stop"]
    slot_qty = int(per // entry)
    if slot_qty < 1:
        return None, f"${per:.2f}/slot < 1 share @ ${entry:.2f}"
    qty = max(1, slot_qty // 2)                      # half-entry (add completes to full)
    spread = entry - stop
    risk_pct = spread / entry * 100 if entry > 0 else 0
    if risk_pct > MAX_RISK_PCT:
        return None, f"risk {risk_pct:.1f}% > {MAX_RISK_PCT}% cap"
    if spread < MIN_BAR_RANGE:
        return None, f"bar range ${spread:.2f} < ${MIN_BAR_RANGE:.2f}"
    return {
        "order_id": f"{user}:{sgn['broadcast_id']}",
        "broadcast_id": sgn["broadcast_id"],
        "user": user, "chat_id": str(ucfg.get("telegram_chat", "")),
        "bot": "family_bot",
        "symbol": sgn["symbol"], "side": "buy", "type": "stop",
        "price": round(entry, 2), "qty": qty, "stop": round(stop, 2), "entry": round(entry, 2),
        "slot_usd": round(per, 2), "day_budget_usd": budget,
        "notional_usd": round(qty * entry, 2),
        "status": "pending", "dry": True,
    }, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="pilot")
    ap.add_argument("--date", required=True)
    ap.add_argument("--users", default=DEFAULT_USERS)
    ap.add_argument("--broadcast-dir", default=DEFAULT_BCAST)
    a = ap.parse_args()

    ucfg = load_user(a.users, a.user)
    bpath = os.path.join(a.broadcast_dir, f"signals_{a.date}.json")
    if not os.path.exists(bpath):
        raise SystemExit(f"[follower] no broadcast at {bpath} — run producer first")
    signals = json.load(open(bpath))["signals"]
    max_trades = int(ucfg.get("max_trades", 5))
    taken = signals[:max_trades]                     # first-to-arm, mirrors his cap

    pend_dir = os.path.join(STATE, "remote_confirm", a.user, "pending")
    os.makedirs(pend_dir, exist_ok=True)
    staged, skipped = [], []
    for sgn in taken:
        order, reason = size_order(sgn, a.user, ucfg)
        if order is None:
            skipped.append((sgn["symbol"], reason)); continue
        fpath = os.path.join(pend_dir, order["order_id"].replace(":", "_") + ".json")
        if os.path.exists(fpath):
            staged.append(json.load(open(fpath)))   # already staged/decided — never reset its status
            continue
        with open(fpath, "w") as f:
            json.dump(order, f, indent=2)
        staged.append(order)

    print(f"[follower] {a.user} {a.date}: staged {len(staged)} DRY order(s) -> {pend_dir}")
    for o in staged:
        print(f"  {o['symbol']:6} buy {o['qty']} @ {o['price']} stop {o['stop']} "
              f"| slot ${o['slot_usd']} notional ${o['notional_usd']}")
    for sym, r in skipped:
        print(f"  SKIP {sym}: {r}")


if __name__ == "__main__":
    main()
