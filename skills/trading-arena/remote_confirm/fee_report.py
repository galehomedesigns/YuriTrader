#!/usr/bin/env python3
"""fee_report.py — per-user bid totals for fee calculation.

Aggregates Telegram.userBids by user over an optional date range, summing all THREE
bid variables (slot, notional, filled) so the operator can apply his fee on whichever
basis. Reads with secondaryPreferred so it works even while Cluster0 has no primary.

Usage: fee_report.py [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--user U] [--action approve]
"""
import argparse
import datetime
import os
import sys
import time


def load_env(path="/home/tonygale/openclaw/.env"):
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main():
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom")
    ap.add_argument("--to", dest="dto")
    ap.add_argument("--user")
    ap.add_argument("--action", default="approve", help="approve|skip|all (default approve)")
    a = ap.parse_args()

    from pymongo import MongoClient, ReadPreference
    c = MongoClient(os.environ["DB_URL"], serverSelectionTimeoutMS=6000,
                    read_preference=ReadPreference.SECONDARY_PREFERRED)
    coll = c[os.environ.get("MONGODB_DB", "Telegram")][os.environ.get("MONGODB_BIDS_COLLECTION", "userBids")]

    match = {}
    if a.action != "all":
        match["action"] = a.action
    if a.user:
        match["user"] = a.user
    ts = {}
    if a.dfrom:
        ts["$gte"] = datetime.datetime.fromisoformat(a.dfrom).replace(tzinfo=datetime.timezone.utc)
    if a.dto:
        ts["$lte"] = datetime.datetime.fromisoformat(a.dto).replace(tzinfo=datetime.timezone.utc)
    if ts:
        match["ts_sent"] = ts

    pipeline = [
        {"$match": match},
        {"$group": {"_id": "$user",
                    "slot": {"$sum": "$slot_usd"},
                    "notional": {"$sum": "$notional_usd"},
                    "filled": {"$sum": {"$ifNull": ["$filled_usd", 0]}},
                    "bids": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]
    from pymongo.errors import PyMongoError
    rows = None
    for attempt in range(6):                 # ride out a Cluster0 no-primary flap
        try:
            rows = list(coll.aggregate(pipeline))
            break
        except PyMongoError as e:
            print(f"  (cluster flap, retry {attempt + 1}/6: {str(e)[:50]})", file=sys.stderr)
            time.sleep(5)
    if rows is None:
        sys.exit("cluster unreachable (flapping) — bids are stored; re-run shortly.")
    rng = f"{a.dfrom or 'all'} .. {a.dto or 'now'}"
    print(f"Per-user bid totals ({rng}, action={a.action})")
    print(f"{'user':14} {'bids':>5} {'Σ slot $':>12} {'Σ notional $':>14} {'Σ filled $':>12}")
    print("-" * 62)
    tslot = tnot = tfill = tn = 0
    for r in rows:
        print(f"{str(r['_id']):14} {r['bids']:>5} {r['slot']:>12.2f} {r['notional']:>14.2f} {r['filled']:>12.2f}")
        tslot += r["slot"]; tnot += r["notional"]; tfill += r["filled"]; tn += r["bids"]
    print("-" * 62)
    print(f"{'TOTAL':14} {tn:>5} {tslot:>12.2f} {tnot:>14.2f} {tfill:>12.2f}")
    print("\nFee basis is yours to pick from these columns.")


if __name__ == "__main__":
    main()
