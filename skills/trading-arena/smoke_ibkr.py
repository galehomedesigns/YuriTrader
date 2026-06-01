#!/usr/bin/env python3
"""IBKR connectivity smoke test.

Confirms that IB Gateway is reachable, that ib_async can authenticate, and
that the account exposes the basics we need (managed accounts, balances,
positions, a quote). Does NOT place any orders.

Run AFTER:
  1. docker compose -f infra/ib-gateway/docker-compose.yml up -d
  2. IB Gateway has logged in (check `docker logs ib-gateway` for
     "Login has completed" — usually 30-90s after start, plus 2FA approval
     on the IBKR Mobile app).

Usage:
    python3 smoke_ibkr.py
    python3 smoke_ibkr.py --symbol AAPL   # also fetch a quote
"""
import argparse
import os
import sys

from ib_async import IB, Stock, util

util.patchAsyncio()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("IBKR_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("IBKR_PORT", "4002")))
    parser.add_argument("--client-id", type=int,
                        default=int(os.environ.get("IBKR_CLIENT_ID", "99")))
    parser.add_argument("--symbol", default="AAPL",
                        help="Symbol to fetch a snapshot quote for")
    args = parser.parse_args()

    print(f"Connecting to {args.host}:{args.port} (clientId={args.client_id}) ...")
    ib = IB()
    try:
        ib.connect(args.host, args.port, clientId=args.client_id, timeout=20)
    except Exception as e:
        print(f"  FAIL: {e}", file=sys.stderr)
        print("  Hints:", file=sys.stderr)
        print("    - Is ib-gateway container up?  docker ps | grep ib-gateway", file=sys.stderr)
        print("    - Has Gateway finished login?  docker logs --tail 50 ib-gateway", file=sys.stderr)
        print("    - Is the API port open?        ss -tln | grep ':4002\\|:4001'", file=sys.stderr)
        sys.exit(1)

    print(f"  connected={ib.isConnected()}  server_version={ib.client.serverVersion()}")

    accounts = ib.managedAccounts()
    print(f"\nManaged accounts: {accounts}")
    if not accounts:
        print("  FAIL: Gateway returned no accounts. Likely still logging in.", file=sys.stderr)
        ib.disconnect()
        sys.exit(1)

    print("\nAccount summary:")
    for v in ib.accountSummary(accounts[0]):
        if v.tag in (
            "NetLiquidation", "TotalCashValue", "AvailableFunds",
            "BuyingPower", "AccountType",
        ):
            print(f"  {v.tag:<18} {v.value:<20} {v.currency}")

    positions = ib.positions(accounts[0])
    print(f"\nOpen positions: {len(positions)}")
    for p in positions[:5]:
        print(f"  {p.contract.symbol:<6} qty={p.position} avg={p.avgCost}")

    if args.symbol:
        print(f"\nQuote snapshot for {args.symbol}:")
        contract = Stock(args.symbol, "SMART", "USD")
        try:
            ib.qualifyContracts(contract)
            ticker = ib.reqMktData(contract, "", snapshot=True, regulatorySnapshot=False)
            ib.sleep(2)
            print(f"  bid={ticker.bid}  ask={ticker.ask}  last={ticker.last}  "
                  f"close={ticker.close}")
            if all(v is None or v != v for v in (ticker.bid, ticker.ask, ticker.last)):
                print("  (no real-time data — likely missing market-data subscription; "
                      "orders still work)")
        except Exception as e:
            print(f"  quote fetch failed: {e}")

    ib.disconnect()
    print("\nOK — smoke test passed.")


if __name__ == "__main__":
    main()
