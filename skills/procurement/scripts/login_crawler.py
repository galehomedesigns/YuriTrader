#!/usr/bin/env python3
"""Browser-based crawler that logs into procurement portals using OpenClaw's browser.

Uses OpenClaw's built-in Playwright browser to authenticate into portals that require
login, then scrapes tenders (open + closed/awarded).

Usage:
    python3 login_crawler.py                    # Crawl all portals
    python3 login_crawler.py --portal bonfire   # Crawl specific portal
    python3 login_crawler.py --portal bc-bid
    python3 login_crawler.py --portal civicinfo
    python3 login_crawler.py --dry-run          # Parse only, don't save
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
CREDS_FILE = "/data/.openclaw/workspace/projects/procurement-pipeline/credentials.json"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

PORTALS = {
    "bonfire": {
        "name": "Bonfire",
        "login_url": "https://login.bonfirehub.ca/login",
        "tenders_url": "https://bonfirehub.ca/opportunities?status=open",
        "closed_url": "https://bonfirehub.ca/opportunities?status=closed",
        "platform": "bonfire",
    },
    "bc-bid": {
        "name": "BC Bid",
        "login_url": "https://www.bcbid.gov.bc.ca/page.aspx/en/usr/login",
        "tenders_url": "https://www.bcbid.gov.bc.ca/page.aspx/en/buy/homepublic",
        "closed_url": "https://www.bcbid.gov.bc.ca/page.aspx/en/buy/homepublic?status=closed",
        "platform": "bcbid",
    },
    "civicinfo": {
        "name": "CivicInfo BC",
        "login_url": "https://www.civicinfo.bc.ca/login",
        "tenders_url": "https://www.civicinfo.bc.ca/bids",
        "closed_url": "https://www.civicinfo.bc.ca/bids?status=closed",
        "platform": "civicinfo",
    },
}


def load_credentials():
    """Load portal credentials from credentials.json."""
    if not os.path.exists(CREDS_FILE):
        print(f"Error: credentials file not found: {CREDS_FILE}", file=sys.stderr)
        sys.exit(1)
    with open(CREDS_FILE) as f:
        return json.load(f)


def get_portal_creds(creds, portal_name):
    """Get credentials for a specific portal."""
    for portal in creds.get("portals", []):
        if portal["name"].lower() == portal_name.lower():
            return portal
    return None


def browser_cmd(cmd):
    """Run an OpenClaw browser command and return output."""
    full_cmd = f"openclaw browser {cmd}"
    result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True, timeout=90)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if stderr and "model-providers" not in stderr:
            print(f"  Browser error: {stderr[:200]}", file=sys.stderr)
        return None
    output = result.stdout.strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return output


def browser_navigate(url):
    """Navigate to URL and wait for load."""
    browser_cmd(f'navigate "{url}"')
    time.sleep(2)


def browser_snapshot():
    """Take an accessibility tree snapshot of the current page."""
    result = browser_cmd("snapshot --format aria --limit 500")
    return result if result else ""


def browser_fill_and_submit(email, password):
    """Fill login form and submit. Tries common form patterns."""
    # Take snapshot to find form fields
    snap = browser_snapshot()
    if not snap:
        print("  Could not get page snapshot", file=sys.stderr)
        return False

    snap_text = json.dumps(snap) if isinstance(snap, dict) else str(snap)

    # Try to find email/username and password fields by ref
    # Look for input fields in the snapshot
    email_refs = re.findall(r'ref["\s:=]+(\d+).*?(?:email|user|login|username)', snap_text, re.IGNORECASE)
    pass_refs = re.findall(r'ref["\s:=]+(\d+).*?(?:password|pass)', snap_text, re.IGNORECASE)

    if not email_refs:
        # Try reverse pattern
        email_refs = re.findall(r'(?:email|user|login|username).*?ref["\s:=]+(\d+)', snap_text, re.IGNORECASE)
    if not pass_refs:
        pass_refs = re.findall(r'(?:password|pass).*?ref["\s:=]+(\d+)', snap_text, re.IGNORECASE)

    if email_refs and pass_refs:
        email_ref = email_refs[0]
        pass_ref = pass_refs[0]
        browser_cmd(f'type {email_ref} "{email}"')
        time.sleep(0.5)
        browser_cmd(f'type {pass_ref} "{password}" --submit')
        time.sleep(3)
        return True

    # Fallback: try fill command with field descriptors
    fields = json.dumps([
        {"ref": "email", "value": email},
        {"ref": "password", "value": password},
    ])
    browser_cmd(f"fill --fields '{fields}'")
    time.sleep(1)
    browser_cmd("press Enter")
    time.sleep(3)
    return True


def parse_tenders_from_snapshot(snapshot, portal_config, status="open"):
    """Parse tender data from a browser snapshot."""
    snap_text = json.dumps(snapshot) if isinstance(snapshot, (dict, list)) else str(snapshot)
    tenders = []

    # Generic pattern: look for tender-like entries with title + organization + date
    # This is intentionally broad — each portal may need specific tuning
    lines = snap_text.split("\\n") if "\\n" in snap_text else snap_text.split("\n")

    current_tender = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Look for tender titles (usually links with descriptive text)
        title_match = re.search(r'(?:link|heading|text)["\s:]*"?([^"]{20,200})', line, re.IGNORECASE)
        if title_match and any(kw in line.lower() for kw in ["rfp", "rfq", "rft", "tender", "request", "supply", "construction", "service"]):
            if current_tender.get("title"):
                tenders.append(current_tender)
            current_tender = {
                "title": title_match.group(1).strip(),
                "status": status,
                "platform": portal_config["platform"],
                "province": "BC",
            }

        # Look for dates
        date_match = re.search(r'(\d{4}-\d{2}-\d{2}|\w+ \d{1,2},? \d{4})', line)
        if date_match and current_tender:
            current_tender.setdefault("closing_date", date_match.group(1))

        # Look for organization names
        org_match = re.search(r'(?:city of|district of|regional|municipality|province|government)\s+[\w\s]+', line, re.IGNORECASE)
        if org_match and current_tender:
            current_tender.setdefault("organization", org_match.group(0).strip())

    if current_tender.get("title"):
        tenders.append(current_tender)

    return tenders


def supabase_post(table, data, upsert=False):
    """Post data to Supabase."""
    import urllib.request
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    if upsert:
        url += "?on_conflict=title,organization"
    prefer = "return=representation"
    if upsert:
        prefer += ",resolution=merge-duplicates"
    headers = {**HEADERS, "Prefer": prefer}
    body = json.dumps(data if isinstance(data, list) else [data]).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  Supabase error: {str(e)[:200]}", file=sys.stderr)
        return None


def crawl_portal(portal_key, dry_run=False):
    """Crawl a single portal: login, scrape open tenders, scrape closed tenders."""
    if portal_key not in PORTALS:
        print(f"Unknown portal: {portal_key}. Options: {', '.join(PORTALS.keys())}")
        return {"open": 0, "closed": 0, "errors": 1}

    portal = PORTALS[portal_key]
    creds = load_credentials()
    portal_creds = get_portal_creds(creds, portal["name"])

    if not portal_creds:
        print(f"No credentials found for {portal['name']}")
        return {"open": 0, "closed": 0, "errors": 1}

    print(f"\n{'='*50}")
    print(f"Crawling: {portal['name']}")
    print(f"{'='*50}")

    # Start browser
    browser_cmd("start")
    time.sleep(2)

    results = {"open": 0, "closed": 0, "errors": 0}

    try:
        # Step 1: Login
        print(f"  Logging in to {portal['login_url']}...")
        browser_navigate(portal["login_url"])
        success = browser_fill_and_submit(portal_creds["email"], portal_creds["password"])
        if not success:
            print(f"  Login failed for {portal['name']}", file=sys.stderr)
            results["errors"] += 1
            return results

        time.sleep(3)

        # Step 2: Scrape open tenders
        print(f"  Scraping open tenders...")
        browser_navigate(portal["tenders_url"])
        time.sleep(3)
        snap = browser_snapshot()
        if snap:
            open_tenders = parse_tenders_from_snapshot(snap, portal, status="open")
            print(f"  Found {len(open_tenders)} open tenders")
            results["open"] = len(open_tenders)

            if not dry_run and open_tenders:
                for t in open_tenders:
                    t["source"] = portal["name"]
                    supabase_post("tenders", t, upsert=True)

        # Step 3: Scrape closed/awarded tenders
        if portal.get("closed_url"):
            print(f"  Scraping closed tenders...")
            browser_navigate(portal["closed_url"])
            time.sleep(3)
            snap = browser_snapshot()
            if snap:
                closed_tenders = parse_tenders_from_snapshot(snap, portal, status="closed")
                print(f"  Found {len(closed_tenders)} closed tenders")
                results["closed"] = len(closed_tenders)

                if not dry_run and closed_tenders:
                    for t in closed_tenders:
                        t["source"] = portal["name"]
                        supabase_post("tenders", t, upsert=True)

    except Exception as e:
        print(f"  Error crawling {portal['name']}: {e}", file=sys.stderr)
        results["errors"] += 1

    return results


def main():
    parser = argparse.ArgumentParser(description="Browser-based procurement portal crawler")
    parser.add_argument("--portal", type=str, help=f"Portal to crawl: {', '.join(PORTALS.keys())}")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, don't save")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_KEY required", file=sys.stderr)
        sys.exit(1)

    portals_to_crawl = [args.portal] if args.portal else list(PORTALS.keys())
    all_results = {}

    for portal_key in portals_to_crawl:
        all_results[portal_key] = crawl_portal(portal_key, dry_run=args.dry_run)

    # Summary
    print(f"\n{'='*50}")
    print("Login Crawler Summary")
    print(f"{'='*50}")
    total_open = 0
    total_closed = 0
    total_errors = 0
    for portal, res in all_results.items():
        print(f"  {PORTALS[portal]['name']}: {res['open']} open, {res['closed']} closed"
              + (f", {res['errors']} errors" if res['errors'] else ""))
        total_open += res["open"]
        total_closed += res["closed"]
        total_errors += res["errors"]

    print(f"  Total: {total_open} open, {total_closed} closed, {total_errors} errors")
    print(json.dumps({"total_open": total_open, "total_closed": total_closed, "errors": total_errors}))


if __name__ == "__main__":
    main()
