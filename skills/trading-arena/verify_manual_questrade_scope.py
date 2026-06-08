#!/usr/bin/env python3
"""Verify whether the MANUAL stock-concierge path can actually place orders.

Background
----------
`execute_manual_trade()` -> `place_market_order(symbol, side, qty,
validate=...)` in shared/questrade_executor.py. When `validate=True` (the
default, and the path taken whenever the two manual gates are NOT both open)
it returns a dry-run dict *without ever POSTing to Questrade*. So the manual
"dry run" is local-only and can never reveal whether the API token is
actually authorized to place an order. The real manual path
(`validate=False`) POSTs straight to `/v1/accounts/{id}/orders` — a REAL,
placing order — so it can't be used as a safe probe either.

This script closes that gap. It rebuilds the EXACT order body that the
manual live path (`place_market_order(validate=False)`) would POST, then
sends it to Questrade's official **non-placing** preview endpoint
`POST /v1/accounts/{id}/orders/impact`. That endpoint is gated by the SAME
OAuth order scope as `/orders` but creates no order, so:

  * Preview JSON returned        -> manual order scope is GOOD (the manual
                                    concierge's real POST would be accepted,
                                    subject only to buying power / hours).
  * HTTP 403 / code 1016         -> manual path is BLOCKED, exactly like the
                                    autonomous path (read-only token).

It uses the same QuestradeExecutor (same token cache / auth) the live
concierge uses, so the result reflects the concierge's real credentials.

GUARANTEE: this script only ever POSTs to `.../orders/impact`. It never
calls `.../orders`. It places nothing, regardless of gate state, args, or
market hours. Safe to run any time.

Usage
-----
  cd /home/tonygale/openclaw && set -a && . ./.env && set +a && \
    .venv/bin/python skills/trading-arena/verify_manual_questrade_scope.py
  # optional: --symbol MSFT --side Buy --qty 1

Exit codes: 0 = scope OK, 1 = blocked (1016), 2 = other error.
"""
import argparse
import json
import os
import sys

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)


def _load_env():
    """Mirror firing_report.py: populate os.environ from the repo .env so the
    script works under cron / bare shells without a manual `set -a`."""
    env_file = "/home/tonygale/openclaw/.env"
    if not os.path.exists(env_file):
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k and v:
                os.environ.setdefault(k, v)


_load_env()

from shared.questrade_executor import (  # noqa: E402
    QuestradeExecutor,
    QuestradeExecutorError,
)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="AAPL", help="symbol to preview (default AAPL)")
    ap.add_argument("--side", default="Buy", help="Buy or Sell (default Buy)")
    ap.add_argument("--qty", type=int, default=1, help="whole-share qty (default 1)")
    args = ap.parse_args()

    side_norm = args.side.capitalize()
    if side_norm not in ("Buy", "Sell"):
        print(f"side must be Buy or Sell (got {args.side})", file=sys.stderr)
        return 2
    if args.qty <= 0:
        print(f"qty must be > 0 (got {args.qty})", file=sys.stderr)
        return 2

    g1 = os.environ.get("QUESTRADE_ALLOW_TRADING", "false").lower() == "true"
    g2 = os.environ.get("MANUAL_STOCK_TRADING_ENABLED", "false").lower() == "true"
    print("=== Manual Questrade order-scope verifier (NON-PLACING) ===")
    print(f"  manual gates: QUESTRADE_ALLOW_TRADING={g1}  "
          f"MANUAL_STOCK_TRADING_ENABLED={g2}")
    print("  (gates do not affect this probe — it only ever hits /orders/impact)")
    print(f"  probe order : {side_norm} {args.qty} {args.symbol} Market/Day\n")

    ex = QuestradeExecutor()
    try:
        # Reproduce place_market_order(validate=False)'s pre-flight + body
        # EXACTLY (whole-share int qty, Market, Day, AUTO routes) so the scope
        # tested is precisely the manual concierge's order path.
        quote = ex.get_quote(args.symbol)
        price = quote["ask"] if side_norm == "Buy" else quote["bid"]
        if price <= 0:
            price = quote["last"]
        if price <= 0:
            print(f"no valid price for {args.symbol}", file=sys.stderr)
            return 2
        currency = quote["currency"]
        sid = ex.resolve_symbol_id(args.symbol)
        acct = ex.get_account_id()
        order = {
            "accountNumber": acct,
            "symbolId": sid,
            "quantity": int(args.qty),
            "orderType": "Market",
            "timeInForce": "Day",
            "action": side_norm,
            "primaryRoute": "AUTO",
            "secondaryRoute": "AUTO",
        }
        print(f"  account={acct}  symbolId={sid}  ~price=${price:.2f} {currency}")
        print("  POST /v1/accounts/%s/orders/impact  (non-placing preview)\n" % acct)

        impact = ex._post(f"/v1/accounts/{acct}/orders/impact", order)
    except QuestradeExecutorError as e:
        msg = str(e)
        if "1016" in msg or "out of allowed OAuth scopes" in msg:
            print("VERDICT: ❌ STILL BLOCKED — manual API order path is NOT "
                  "authorized.")
            print("         Same read-only scope wall as the autonomous path "
                  "(HTTP 403 / code 1016).")
            print("         The manual concierge's 'dry run' only looked OK "
                  "because it never POSTs.")
            print(f"\n  raw: {msg}")
            return 1
        print("VERDICT: ⚠️  INCONCLUSIVE — call failed for a non-scope reason "
              "(auth/network/symbol).")
        print(f"\n  raw: {msg}")
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"VERDICT: ⚠️  INCONCLUSIVE — unexpected error: {e}")
        return 2

    print("VERDICT: ✅ SCOPE OK — Questrade accepted a non-placing order "
          "preview.")
    print("         The manual concierge's real POST /orders would be "
          "authorized")
    print("         (subject only to live buying power + market hours).")
    print("\n  /orders/impact response:")
    print(json.dumps(impact, indent=2)[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
