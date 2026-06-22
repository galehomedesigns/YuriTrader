#!/usr/bin/env python3
"""ISOLATED IBKR historical-data probe — backtest data only.

Completely separate from the live trading path: imports NOTHING from opening_agent
/ tv_* / questrade / CDP, places NO orders, uses a DEDICATED clientId (default 88,
not the live executor's 17), and only ever calls reqHistoricalData (read-only).
Talks solely to the IB Gateway API port (127.0.0.1:4001) — nothing else.

Purpose: answer the open question "does reqHistoricalData return real 2-min bars
on this delayed/unsubscribed account?" before we build any IBKR-sourced backfill.

Run AFTER the gateway is up + logged in (2FA approved):
    .venv/bin/python skills/trading-arena/ibkr_history/probe.py
"""
import os
import sys

from ib_async import IB, Stock

HOST = os.environ.get("IBKR_HOST", "127.0.0.1")
PORT = int(os.environ.get("IBKR_PORT", "4001"))
CLIENT_ID = int(os.environ.get("IBKR_PROBE_CLIENT_ID", "88"))   # dedicated, != live 17
SYMBOL = os.environ.get("IBKR_PROBE_SYMBOL", "AAPL")

errors = []


def _on_error(reqId, code, msg, *a):
    # 2106/2104/2158 are benign "data farm connected" notices
    if code not in (2104, 2106, 2158, 2107, 2119):
        errors.append((code, msg))
        print(f"  [ib error {code}] {msg}", file=sys.stderr)


def main():
    ib = IB()
    ib.errorEvent += _on_error
    print(f"Connecting {HOST}:{PORT} clientId={CLIENT_ID} (read-only historical probe) ...")
    try:
        ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=25)
    except Exception as e:                                       # noqa: BLE001
        print(f"  CONNECT FAIL: {e}", file=sys.stderr)
        print("  -> is the gateway up + logged in?  docker ps | grep ib-gateway ; "
              "docker logs --tail 40 ib-gateway", file=sys.stderr)
        sys.exit(2)
    print(f"  connected={ib.isConnected()}  accounts={ib.managedAccounts()}")

    ib.reqMarketDataType(3)            # delayed — this account has no real-time sub
    c = Stock(SYMBOL, "SMART", "USD")
    try:
        ib.qualifyContracts(c)
    except Exception as e:                                       # noqa: BLE001
        print(f"  qualify FAIL: {e}", file=sys.stderr)

    def grab(label, end, dur, size):
        print(f"\n[{label}] end={end or 'now'} dur={dur} size={size}")
        try:
            bars = ib.reqHistoricalData(c, endDateTime=end, durationStr=dur,
                                        barSizeSetting=size, whatToShow="TRADES",
                                        useRTH=True, formatDate=1, timeout=60)
        except Exception as e:                                   # noqa: BLE001
            print(f"  EXCEPTION: {e}")
            return None
        if not bars:
            print("  -> 0 bars (permission/availability issue — see errors above)")
            return bars
        print(f"  -> {len(bars)} bars | {bars[0].date} … {bars[-1].date} | "
              f"last close {bars[-1].close}")
        return bars

    # 1) recent — the permission check (does delayed/unsub serve historical at all?)
    recent = grab("recent 2-min", "", "5 D", "2 mins")
    # 2) deep — ~1 year back, to gauge how far history reaches
    deep_end = os.environ.get("IBKR_PROBE_DEEP_END", "20250620 16:00:00 US/Eastern")
    grab("deep 2-min (~1yr ago)", deep_end, "1 M", "2 mins")

    ib.disconnect()
    print("\n=== VERDICT ===")
    if recent:
        print(f"HISTORICAL WORKS on this account ✅ — got {len(recent)} recent 2-min bars.")
    else:
        print("HISTORICAL BLOCKED ✗ — no bars returned; "
              f"errors={[e[0] for e in errors] or 'none surfaced'}")


if __name__ == "__main__":
    main()
