#!/usr/bin/env python3
"""Pre-open ACCOUNT-CLEAN check — runs 45 min before the open (8:45 ET / 6:45
server) and Telegrams whether anything is resting on the Questrade account, so a
leftover working order can't fire unexpectedly at 9:30.

Authoritative source = Questrade read-only REST (get_open_orders / get_positions),
NOT the TradingView DOM — the broker panel renders inconsistently and can't be
trusted for an unattended check. Read-only: places/cancels NOTHING.

  preopen_account_check.py            # check + Telegram
  preopen_account_check.py --print    # check + stdout only (no Telegram)
"""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
from shared.questrade_executor import QuestradeExecutor          # noqa: E402
from opening_agent.run_opening_scan import send_message          # noqa: E402


def _fmt_order(o):
    sym = o.get("symbol") or o.get("symbolId") or "?"
    side = o.get("side", "?")
    otype = o.get("orderType", "?")
    qty = o.get("totalQuantity", o.get("openQuantity", "?"))
    px = o.get("stopPrice") or o.get("limitPrice")
    state = o.get("state", "?")
    pxbit = f" @ {px}" if px else ""
    return f"{sym} {side} {otype} {qty}{pxbit} [{state}]"


def _fmt_pos(p):
    sym = p.get("symbol") or "?"
    qty = p.get("openQuantity")
    return f"{sym}×{qty}"


def build_report():
    et = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M")
    head = f"🧾 <b>Opening Power — account check</b>  <i>{et} ET</i>"
    try:
        ex = QuestradeExecutor()
        orders = ex.get_open_orders()
        positions = ex.get_positions()
    except Exception as e:                       # a failed check is NOT a clean one
        return (f"{head}\n\n⚠️ <b>Could not verify account</b> — Questrade API "
                f"error: {e}\nResolve before the open; can't confirm it's clean.", False)

    pos_line = (f"Holding {len(positions)} position(s): "
                + ", ".join(_fmt_pos(p) for p in positions)) if positions else "No open positions."

    if orders:
        lines = [head, "",
                 f"⚠️ <b>NOT CLEAN — {len(orders)} working order(s) resting:</b>"]
        lines += [f"  • {_fmt_order(o)}" for o in orders]
        lines += ["", "Cancel any you don't want before 9:30.", pos_line]
        return "\n".join(lines), False

    return (f"{head}\n\n✅ <b>Account clean</b> — 0 working orders resting.\n{pos_line}", True)


def main():
    msg, clean = build_report()
    if "--print" in sys.argv:
        print(msg)
    else:
        send_message(msg)
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
