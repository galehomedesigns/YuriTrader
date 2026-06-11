#!/usr/bin/env python3
"""Live order-path SMOKE TEST — prove orders place + cancel before any real entry.

Places ONE tiny BUY LIMIT far below market (1 share @ $1.00 of a >$5 stock), so it
REST in the book and CANNOT fill, confirms it appears, cancels it, confirms it's
gone. On success it touches a flag file that live_executor requires before it will
transmit a real bracket — so the agent physically cannot place a real entry until
this has passed at least once.

Run AFTER you've switched the gateway to your live account (it tests whatever
account the gateway is logged into):

    .venv/bin/python skills/trading-arena/opening_agent/smoke_test.py
"""
import os
import sys

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
SMOKE_FLAG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "logs", "opening_smoke_ok.flag")
TEST_SYMBOL = os.environ.get("OPENING_SMOKE_SYMBOL", "F")   # Ford, ~$10 — $1 buy can't fill


def main():
    from ib_async import IB, Stock, LimitOrder, util
    util.patchAsyncio()
    host = os.environ.get("IBKR_HOST", "127.0.0.1")
    port = int(os.environ.get("IBKR_PORT", "4002"))
    ib = IB()
    ib.connect(host, port, clientId=int(os.environ.get("OPENING_SMOKE_CLIENT_ID", "25")),
               timeout=20)
    acct = ib.managedAccounts()
    print(f"connected: {ib.isConnected()} | account(s): {acct} | port {port} "
          f"({'LIVE' if port == 4001 else 'PAPER'})")

    contract = Stock(TEST_SYMBOL, "SMART", "USD")
    ib.qualifyContracts(contract)
    order = LimitOrder("BUY", 1, 1.00)          # 1 share @ $1 — rests, cannot fill
    trade = ib.placeOrder(contract, order)
    ib.sleep(2)
    placed_ok = trade.orderStatus.status in ("PreSubmitted", "Submitted", "ApiPending")
    print(f"placed test order {TEST_SYMBOL} 1@$1.00 -> status={trade.orderStatus.status} "
          f"({'OK' if placed_ok else 'UNEXPECTED'})")

    ib.cancelOrder(order)
    ib.sleep(2)
    cancelled_ok = trade.orderStatus.status in ("Cancelled", "ApiCancelled", "PendingCancel")
    open_ids = [t.order.orderId for t in ib.openTrades()]
    gone = trade.order.orderId not in open_ids
    print(f"cancel -> status={trade.orderStatus.status} | gone_from_book={gone}")
    ib.disconnect()

    if placed_ok and (cancelled_ok or gone):
        os.makedirs(os.path.dirname(SMOKE_FLAG), exist_ok=True)
        open(SMOKE_FLAG, "w").write(f"smoke ok on port {port} acct {acct}\n")
        print(f"\n✅ SMOKE TEST PASSED — order path places + cancels. Flag written:\n   {SMOKE_FLAG}")
        print("   live_executor will now permit real entries (once OPENING_ALLOW_TRADING=true).")
        return 0
    print("\n❌ SMOKE TEST FAILED — do NOT arm. Order did not place/cancel as expected.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
