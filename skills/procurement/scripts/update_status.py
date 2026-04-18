#!/usr/bin/env python3
"""Update tender statuses: mark expired tenders as closed, check for awarded status.

Usage:
    python3 update_status.py              # Update all expired tenders
    python3 update_status.py --dry-run    # Show what would change without writing
    python3 update_status.py --stats      # Show status breakdown
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def supabase_get(path):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"  GET error: {e.read().decode()[:200]}", file=sys.stderr)
        return []


def supabase_patch(table, match_params, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}?{match_params}"
    headers = {**HEADERS, "Prefer": "return=representation"}
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="PATCH")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"  PATCH error: {e.read().decode()[:200]}", file=sys.stderr)
        return []


def get_status_stats():
    """Get tender count by status."""
    statuses = ["open", "closed", "awarded", "planned"]
    stats = {}
    for s in statuses:
        rows = supabase_get(f"tenders?status=eq.{s}&select=id&limit=1000")
        stats[s] = len(rows) if rows else 0
    all_rows = supabase_get("tenders?select=id&limit=5000")
    stats["total"] = len(all_rows) if all_rows else 0
    stats["other"] = stats["total"] - sum(v for k, v in stats.items() if k != "total")
    return stats


def update_expired_tenders(dry_run=False):
    """Mark tenders past their closing date as closed."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Find open tenders with closing_date in the past
    expired = supabase_get(
        f"tenders?status=eq.open&closing_date=lt.{now}&select=id,title,organization,closing_date,status"
        f"&order=closing_date.desc&limit=500"
    )

    if not expired:
        print("No expired tenders to update.")
        return 0

    print(f"Found {len(expired)} expired tenders to close.")

    if dry_run:
        for t in expired[:10]:
            print(f"  [DRY RUN] Would close: {t.get('title', 'Unknown')[:60]} "
                  f"({t.get('organization', '?')}) — closed {t.get('closing_date', '?')[:10]}")
        if len(expired) > 10:
            print(f"  ... and {len(expired) - 10} more")
        return len(expired)

    # Batch update: set status = 'closed' for all expired open tenders
    result = supabase_patch(
        "tenders",
        f"status=eq.open&closing_date=lt.{now}",
        {"status": "closed"}
    )

    updated = len(result) if result else 0
    print(f"Updated {updated} tenders from 'open' to 'closed'.")

    # Log summary of recently closed
    for t in (result or [])[:5]:
        print(f"  Closed: {t.get('title', 'Unknown')[:60]} ({t.get('organization', '?')})")
    if updated > 5:
        print(f"  ... and {updated - 5} more")

    return updated


def main():
    parser = argparse.ArgumentParser(description="Update tender statuses")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    parser.add_argument("--stats", action="store_true", help="Show status breakdown")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_KEY required", file=sys.stderr)
        sys.exit(1)

    if args.stats:
        stats = get_status_stats()
        print(json.dumps(stats, indent=2))
        return

    updated = update_expired_tenders(dry_run=args.dry_run)

    # Print final stats
    stats = get_status_stats()
    print(f"\nTender Status Summary:")
    print(f"  Open:    {stats.get('open', 0)}")
    print(f"  Closed:  {stats.get('closed', 0)}")
    print(f"  Awarded: {stats.get('awarded', 0)}")
    print(f"  Total:   {stats.get('total', 0)}")


if __name__ == "__main__":
    main()
