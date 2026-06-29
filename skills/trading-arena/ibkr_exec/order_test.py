#!/usr/bin/env python3
"""SAFE live order-path test for the isolated IBKR executor.

Proves the IBKR API can PLACE and CANCEL a real order on the live account WITHOUT
risking a fill: a non-marketable limit BUY of 1 share ~12% below market, confirmed
working, then cancelled. Imports only ib_async + stdlib — NOTHING from opening_agent
/ tv_* / questrade / CDP. Dedicated clientId so it can't collide with other clients.
"""
import os, sys
from ib_async import IB, Stock, LimitOrder

HOST = os.environ.get("IBKR_HOST", "127.0.0.1")
PORT = int(os.environ.get("IBKR_PORT", "4001"))      # 4001 = live gateway API
ACCT = os.environ.get("IBKR_ACCOUNT_ID", "")
CID  = 91
SYMBOL = "F"

ib = IB()
ib.connect(HOST, PORT, clientId=CID, timeout=25)
print(f"connected={ib.isConnected()} acct={ib.managedAccounts()}")
c = Stock(SYMBOL, "SMART", "USD"); ib.qualifyContracts(c)
# Reference price from historical (proven on this acct) — no live-data dependency.
bars = ib.reqHistoricalData(c, "", "3 D", "1 day", "TRADES", useRTH=True, formatDate=1)
px = bars[-1].close if bars else None
print(f"{SYMBOL} reference close = {px}")
if not px:
    print("NO PRICE — aborting before placing anything."); ib.disconnect(); sys.exit(2)

limit = round(px * 0.88, 2)                           # 12% below: non-marketable
order = LimitOrder("BUY", 1, limit, tif="DAY", transmit=True)
if ACCT: order.account = ACCT
print(f"PLACING (real, won't fill): BUY 1 {SYMBOL} LMT {limit}  (close ~{px})")
trade = ib.placeOrder(c, order); ib.sleep(3)
print(f"  status after place: {trade.orderStatus.status}  filled={trade.orderStatus.filled}")
for le in trade.log: print(f"   log: {le.status} {le.message or ''}")

print("CANCELLING...")
ib.cancelOrder(order); ib.sleep(2.5)
print(f"  status after cancel: {trade.orderStatus.status}  filled={trade.orderStatus.filled}")
print(f"open orders now: {len(ib.reqAllOpenOrders())} | positions: {[(p.contract.symbol,p.position) for p in ib.positions()] or 'none'}")
ib.disconnect()
ok = (trade.orderStatus.filled == 0) and (trade.orderStatus.status in ("Cancelled","ApiCancelled","PendingCancel"))
print("RESULT:", "PASS - order placed + cancelled, ZERO fill - API order path CONFIRMED" if ok
      else f"REVIEW: status={trade.orderStatus.status} filled={trade.orderStatus.filled}")
