#!/usr/bin/env python3
"""
Discover new Canadian procurement and tender websites.
Searches Google News, government directories, and construction associations
for sites offering tenders. Logs new finds to Supabase and adds login-required
sites to the project_tasks to-do list.

Usage:
    python3 discover.py              # Run full discovery scan
    python3 discover.py --report     # Show discovered sites not yet added
"""

import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
FIRECRAWL_KEY = os.environ.get("FIRECRAWL_API_KEY", "")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

# Search queries to find procurement sites
SEARCH_QUERIES = [
    "Canadian municipality procurement tenders bids open",
    "Canada school board procurement RFP tenders 2026",
    "Canadian university procurement bids tenders",
    "Canada hospital health authority procurement tenders",
    "Canadian transit authority procurement bids",
    "Canada construction tenders RFP bidsandtenders",
    "Canadian provincial government procurement portal",
    "Canada municipal procurement bonfire portal",
    "Canadian infrastructure tenders RFP construction",
    "Canada water utility procurement tenders",
    "Canadian housing authority procurement RFP",
    "Canada airport authority procurement tenders",
    "Canadian port authority procurement bids",
    "Canada indigenous community procurement tenders",
    "Canadian college procurement bids tenders",
]

# Known procurement platform patterns
PLATFORM_PATTERNS = {
    "bidsandtenders": re.compile(r'([\w-]+)\.bidsandtenders\.(ca|com)'),
    "bonfire": re.compile(r'([\w-]+)\.bonfirehub\.(ca|com)'),
    "merx": re.compile(r'merx\.com/(\w+)'),
    "civicinfo": re.compile(r'civicinfo\.bc\.ca'),
}

# Sites we already know about — skip these
KNOWN_DOMAINS = set()


def supabase_get(path):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return []


def supabase_post(table, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {**HEADERS, "Prefer": "return=minimal"}
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return True
    except Exception:
        return False


def firecrawl_search(query):
    """Use Firecrawl to search Google and return URLs."""
    if not FIRECRAWL_KEY:
        # Fallback to local logic or mock if key is missing
        return []

    try:
        payload = json.dumps({
            "query": query,
            "limit": 10,
        }).encode()

        req = urllib.request.Request(
            "https://api.firecrawl.dev/v1/search",
            data=payload,
            headers={
                "Authorization": f"Bearer {FIRECRAWL_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            if result.get("success"):
                return result.get("data", [])
            else:
                # If API returns success:false or similar
                print(f"  API Error: {result.get('error', 'Unknown error')}", file=sys.stderr)
    except urllib.error.HTTPError as e:
        if e.code == 402:
            print(f"  Search error: HTTP 402 Payment Required (Firecrawl credits exhausted)", file=sys.stderr)
        else:
            print(f"  Search error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"  Search error: {e}", file=sys.stderr)

    return []


def load_known_sources():
    """Load existing procurement sources to avoid duplicates."""
    global KNOWN_DOMAINS
    sources = supabase_get("procurement_sources?select=url,slug")
    for s in sources:
        url = s.get("url", "")
        try:
            domain = urllib.parse.urlparse(url).hostname or ""
            KNOWN_DOMAINS.add(domain)
        except Exception:
            pass


def classify_site(url, title, description):
    """Classify a discovered site: platform type, province, login required."""
    result = {
        "url": url,
        "title": title,
        "description": description[:300] if description else "",
        "platform": "custom",
        "province": "UNKNOWN",
        "requires_login": False,
        "source_type": "Direct",
    }

    url_lower = url.lower()
    title_lower = (title or "").lower()
    desc_lower = (description or "").lower()
    combined = f"{url_lower} {title_lower} {desc_lower}"

    # Detect platform
    for platform, pattern in PLATFORM_PATTERNS.items():
        if pattern.search(url):
            result["platform"] = platform
            break

    # Detect province
    province_map = {
        "british columbia": "BC", " bc ": "BC", "vancouver": "BC", "victoria bc": "BC",
        "alberta": "AB", "calgary": "AB", "edmonton": "AB",
        "saskatchewan": "SK", "saskatoon": "SK", "regina": "SK",
        "manitoba": "MB", "winnipeg": "MB",
        "ontario": "ON", "toronto": "ON", "ottawa": "ON", "hamilton on": "ON",
        "quebec": "QC", "montreal": "QC", "québec": "QC",
        "nova scotia": "NS", "halifax": "NS",
        "new brunswick": "NB", "moncton": "NB", "fredericton": "NB",
        "newfoundland": "NL", "st. john's": "NL",
        "prince edward": "PE", "charlottetown": "PE",
        "canada": "FED", "federal": "FED", "government of canada": "FED",
    }
    for keyword, prov in province_map.items():
        if keyword in combined:
            result["province"] = prov
            break

    # Detect login requirement
    login_words = ["sign in", "login", "register to view", "create account",
                   "subscription required", "members only", "log in to access"]
    if any(w in combined for w in login_words):
        result["requires_login"] = True

    return result


def add_to_todo(site):
    """Add a login-required site to the project_tasks to-do list."""
    supabase_post("project_tasks", {
        "title": f"Register: {site['title'][:60]}",
        "description": f"{site['url']} — {site['province']} procurement site. Requires login/subscription. Register with decadesdevelopments@gmail.com and set up tender email notifications.",
        "priority": "medium",
        "status": "pending",
        "category": "procurement-registration",
        "assigned_to": "tony",
    })


def cmd_discover():
    """Run full discovery scan."""
    load_known_sources()
    print(f"Known sources: {len(KNOWN_DOMAINS)} domains", file=sys.stderr)

    discovered = []
    seen_urls = set()

    for query in SEARCH_QUERIES:
        print(f"\nSearching: {query}", file=sys.stderr)
        results = firecrawl_search(query)

        for result in results:
            url = result.get("url", "")
            title = result.get("title", "")
            description = result.get("description", "")

            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            # Skip known domains
            try:
                domain = urllib.parse.urlparse(url).hostname or ""
                if domain in KNOWN_DOMAINS:
                    continue
            except Exception:
                continue

            # Skip non-procurement results
            procurement_words = ["tender", "bid", "rfp", "rfq", "procurement",
                                "solicitation", "contract", "opportunity"]
            combined = f"{title} {description}".lower()
            if not any(w in combined for w in procurement_words):
                continue

            site = classify_site(url, title, description)
            discovered.append(site)
            print(f"  NEW: {title[:60]} [{site['province']}] {'(LOGIN)' if site['requires_login'] else ''}", file=sys.stderr)

    # Process discoveries
    new_sources = 0
    login_sites = 0

    for site in discovered:
        if site["requires_login"]:
            add_to_todo(site)
            login_sites += 1
            print(f"  → To-do: {site['title'][:50]} (login required)", file=sys.stderr)
        else:
            # Could auto-add to procurement_sources, but safer to log for review
            supabase_post("system_health_log", {
                "check_name": "procurement.discovery",
                "status": "OK",
                "details": {
                    "url": site["url"],
                    "title": site["title"],
                    "province": site["province"],
                    "platform": site["platform"],
                    "requires_login": False,
                },
                "recommendation": f"New procurement site found: {site['title']} ({site['url']}). Review and add to procurement_sources if relevant.",
            })
            new_sources += 1

    print(f"\n{'='*50}", file=sys.stderr)
    print(f"Discovery complete:", file=sys.stderr)
    print(f"  Searched: {len(SEARCH_QUERIES)} queries", file=sys.stderr)
    print(f"  Results scanned: {len(seen_urls)}", file=sys.stderr)
    print(f"  New sites found: {len(discovered)}", file=sys.stderr)
    print(f"  Open sites (logged for review): {new_sources}", file=sys.stderr)
    print(f"  Login-required (added to to-do): {login_sites}", file=sys.stderr)

    # Output summary as JSON for cron delivery
    summary = {
        "queries": len(SEARCH_QUERIES),
        "scanned": len(seen_urls),
        "discovered": len(discovered),
        "open_sites": new_sources,
        "login_required": login_sites,
        "sites": [{"title": s["title"][:60], "url": s["url"], "province": s["province"],
                   "login": s["requires_login"]} for s in discovered],
    }
    print(json.dumps(summary, indent=2))


def cmd_report():
    """Show recently discovered sites not yet added."""
    logs = supabase_get(
        "system_health_log?check_name=eq.procurement.discovery&status=eq.OK"
        "&select=details,recommendation,timestamp&order=timestamp.desc&limit=50"
    )
    if not logs:
        print("No discovered sites pending review.")
        return

    print(f"Discovered procurement sites pending review ({len(logs)}):\n")
    for log in logs:
        d = log.get("details", {})
        print(f"  [{d.get('province','?')}] {d.get('title','')}")
        print(f"       {d.get('url','')}")
        print(f"       Platform: {d.get('platform','')} | Login: {d.get('requires_login','')}")
        print()


def main():
    parser = argparse.ArgumentParser(description="Discover new Canadian procurement websites")
    parser.add_argument("--report", action="store_true", help="Show discovered sites pending review")
    args = parser.parse_args()

    if args.report:
        cmd_report()
    else:
        cmd_discover()


if __name__ == "__main__":
    main()
