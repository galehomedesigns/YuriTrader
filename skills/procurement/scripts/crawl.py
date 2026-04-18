#!/usr/bin/env python3
"""Crawl Canadian procurement sources and load tenders into Supabase.

Uses Firecrawl for scraping, OpenAI for embeddings.
Parses tenders from: bidsandtenders.ca, CivicInfo BC, Bonfire, SEAO, MERX, custom sites.

Usage:
    python3 crawl.py                        # Crawl all active sources
    python3 crawl.py --province BC          # Crawl only BC sources
    python3 crawl.py --province ON          # Crawl only Ontario sources
    python3 crawl.py --source civicinfo-bc  # Crawl specific source by slug
    python3 crawl.py --embed                # Generate embeddings for tenders missing them
    python3 crawl.py --stats                # Show database stats as JSON (for dashboard)
    python3 crawl.py --dry-run              # Parse only, don't write to DB
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")

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
        print(f"  GET error: {e.read().decode()}", file=sys.stderr)
        return []


def supabase_post(table, data, upsert=False):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    prefer = "return=representation"
    if upsert:
        prefer += ",resolution=merge-duplicates"
    headers = {**HEADERS, "Prefer": prefer}
    body = json.dumps(data if isinstance(data, list) else [data]).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        print(f"  POST error: {err[:200]}", file=sys.stderr)
        return []


def supabase_patch(table, data, query):
    url = f"{SUPABASE_URL}/rest/v1/{table}?{query}"
    headers = {**HEADERS, "Prefer": "return=minimal"}
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="PATCH")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        print(f"  PATCH error: {e.read().decode()[:200]}", file=sys.stderr)
        return None


def firecrawl_scrape(url, wait_for=None):
    """Scrape a URL using firecrawl API. Returns markdown text."""
    api_key = os.environ.get("FIRECRAWL_API_KEY", "")
    if not api_key:
        print("  Error: FIRECRAWL_API_KEY not set", file=sys.stderr)
        return None
    
    payload = {"url": url}
    if wait_for:
        payload["waitFor"] = wait_for
    
    req = urllib.request.Request(
        "https://api.firecrawl.dev/v1/scrape",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode())
            if result.get("success"):
                return result["data"].get("markdown")
            else:
                print(f"  Firecrawl API error: {result.get('error')}", file=sys.stderr)
                return None
    except Exception as e:
        print(f"  Firecrawl request failed: {e}", file=sys.stderr)
        return None


def parse_civicinfo(text):
    """Parse CivicInfo BC bids page."""
    tenders = []
    blocks = re.split(r'\d{2}\.\s+\[Sign in to save\]', text)
    for block in blocks[1:]:
        title_m = re.search(r'\*\*(.+?)\*\*', block)
        if not title_m:
            continue
        title = title_m.group(1).strip()

        type_m = re.search(
            r'\n\s+(Request for (?:Proposals|Quotations|Expression of Interest)|Invitation to (?:Bid|Tender))',
            block
        )
        tender_type = type_m.group(1).strip() if type_m else "Unknown"

        loc_m = re.search(r'([A-Za-z\s]+),\s*BC\s+\d+\s+days?\s+ago', block)
        location = loc_m.group(1).strip() if loc_m else ""

        date_m = re.search(r'Expires:\s+(.+?)$', block, re.MULTILINE)
        closing_str = date_m.group(1).strip() if date_m else ""
        closing_date = parse_date_flexible(closing_str)

        url_m = re.search(r'\(https://www\.civicinfo\.bc\.ca/bids\?bidid=(\d+)\)', block)
        tender_url = f"https://www.civicinfo.bc.ca/bids?bidid={url_m.group(1)}" if url_m else ""

        tenders.append({
            "title": title,
            "tender_type": tender_type,
            "organization": location,
            "location": f"{location}, BC" if location else "BC",
            "closing_date": closing_date,
            "url": tender_url,
            "status": "open",
            "category": categorize(title),
            "raw_text": block[:500],
        })
    return tenders


def parse_bidsandtenders(text, org_name="", province="BC"):
    """Parse bidsandtenders.ca platform pages."""
    tenders = []
    rows = re.findall(
        r'\*\*(.+?)\*\*\s*\|\s*(Open|Closed|Awarded|Planned)\s*\|\s*(.+?)\s*\|\s*(\d+)\s*\|',
        text
    )
    for title, status, closing_str, days in rows:
        title = title.strip()
        ref_m = re.match(r'^([\w]+-[\w]+-?[\w]*)\s*-\s*(.+)', title)
        ref_num = ref_m.group(1) if ref_m else ""
        clean_title = ref_m.group(2).strip() if ref_m else title

        closing_date = parse_bidsandtenders_date(closing_str)

        detail_m = re.search(
            r'Bid Details.*?' + re.escape(title[:30]) + r'.*?\((.+?/Tender/Detail/[^)]+)\)',
            text
        )
        detail_url = detail_m.group(1).split("#")[0] if detail_m else ""

        tenders.append({
            "title": clean_title,
            "reference_number": ref_num if ref_num else None,
            "tender_type": categorize(clean_title),
            "organization": org_name,
            "location": f"{org_name}, {province}",
            "closing_date": closing_date,
            "url": detail_url,
            "status": status.lower(),
            "category": categorize(clean_title),
            "raw_text": f"{ref_num} - {clean_title}" if ref_num else clean_title,
        })
    return tenders


def parse_bonfire(text, org_name="", province="BC"):
    """Parse Bonfire portal pages (e.g., Victoria, Calgary, Regina)."""
    tenders = []
    # Table rows: | Open | RFP 26-014 | **Title** | Date | Days | [View Opportunity](url) |
    rows = re.finditer(
        r'\|\s*Open\s*\|\s*([\w\s-]+?)\s*\|\s*\*\*(.+?)\*\*\s*\|\s*(.+?)\s*\|\s*\d+\s*\|\s*\[View Opportunity\]\((.+?)\)',
        text
    )
    for m in rows:
        ref = m.group(1).strip()
        title = m.group(2).strip()
        date_str = m.group(3).strip()
        url = m.group(4).strip()

        # Parse Bonfire dates like "Mar 17th 2026, 4:00 PM PDT"
        clean_date = re.sub(r'(st|nd|rd|th)', '', date_str)
        clean_date = re.sub(r'\s*(PDT|PST|EDT|EST|MDT|MST|CDT|CST)\s*', '', clean_date).strip()
        closing_date = parse_date_flexible(clean_date)

        # Determine tender type from ref prefix
        tender_type = "General"
        if ref.startswith("RFP"): tender_type = "Request for Proposals"
        elif ref.startswith("RFQ"): tender_type = "Request for Quotations"
        elif ref.startswith("RFO"): tender_type = "Request for Offers"
        elif ref.startswith("T "): tender_type = "Invitation to Tender"
        elif ref.startswith("ITT"): tender_type = "Invitation to Tender"
        elif ref.startswith("EOI"): tender_type = "Expression of Interest"

        tenders.append({
            "title": title,
            "reference_number": ref,
            "tender_type": tender_type,
            "organization": org_name,
            "location": f"{org_name}, {province}",
            "closing_date": closing_date,
            "url": url,
            "status": "open",
            "category": categorize(title),
            "raw_text": f"{ref} - {title}",
        })
    return tenders


def parse_constructconnect(text, org_name="ConstructConnect (DCN)"):
    """Parse ConstructConnect (Daily Commercial News) tender listings.

    Uses the Google News model: index publicly listed titles/dates/URLs,
    link back to original source. Factual data (titles, dates, reference numbers)
    is not copyrightable under Canadian or US law.
    """
    tenders = []
    seen_urls = set()

    # Extract tender titles and URLs from ### heading links
    pattern = r'### \[([^\]]+)\]\((https://canada\.constructconnect\.com/dcn/canadian-construction-tenders/[A-F0-9-]+)\)'
    matches = re.findall(pattern, text)

    # Also extract bid dates near each tender
    bid_dates = re.findall(r'\*\*Bid Date:\*\*\s*(.+?)$', text, re.MULTILINE)

    for i, (title, url) in enumerate(matches):
        if url in seen_urls:
            continue
        seen_urls.add(url)
        title = title.strip()
        if "Menu" in title or "logo" in title or len(title) < 10:
            continue

        # Try to get bid date for this tender
        bid_date = None
        if i < len(bid_dates):
            bid_date = parse_date_flexible(bid_dates[i].strip())

        # Detect tender type from title prefix
        tender_type = "Invitation to Tender"
        t_upper = title.upper()
        if "RFQ" in t_upper: tender_type = "Request for Qualifications"
        elif "RFP" in t_upper: tender_type = "Request for Proposals"
        elif t_upper.startswith("SMS:"): tender_type = "Standing Offer"
        elif "PSO" in t_upper: tender_type = "Public Sector Opportunity"
        elif "REI" in t_upper or "EOI" in t_upper: tender_type = "Expression of Interest"

        tenders.append({
            "title": title[:300],
            "tender_type": tender_type,
            "organization": org_name,
            "location": "Canada",
            "closing_date": bid_date,
            "url": url,
            "status": "open",
            "category": categorize(title),
            "raw_text": title,
        })
    return tenders


def parse_generic(text, org_name="", province="BC"):
    """Generic parser for custom municipal sites."""
    tenders = []
    lines = text.split("\n")
    seen = set()

    for i, line in enumerate(lines):
        link_matches = re.finditer(r'\[([^\]]{15,})\]\((https?://[^)]+)\)', line)
        for m in link_matches:
            title = m.group(1).strip()
            url = m.group(2).strip()

            skip_words = ['login', 'sign in', 'create account', 'home', 'contact', 'about',
                         'skip to', 'menu', 'search', 'français', 'footer', 'header',
                         'privacy', 'terms', 'accessibility', 'sitemap', 'cookie',
                         'register', 'logo', 'bids homepage', 'find more']
            if any(sw in title.lower() for sw in skip_words):
                continue

            tender_words = ['rfp', 'rfq', 'rft', 'tender', 'bid', 'procurement',
                          'supply', 'contract', 'quotation', 'solicitation',
                          'construction', 'services', 'request for', 'eoi']
            context = " ".join(lines[max(0, i-2):min(len(lines), i+3)]).lower()

            if any(tw in title.lower() for tw in tender_words) or any(tw in context for tw in tender_words):
                if title in seen or len(title) < 10:
                    continue
                seen.add(title)

                deadline = None
                for j in range(max(0, i-2), min(len(lines), i+5)):
                    date_match = re.search(r'(\w+ \d{1,2},?\s*\d{4})', lines[j])
                    if date_match:
                        deadline = parse_date_flexible(date_match.group(1))
                        if deadline:
                            break

                tenders.append({
                    "title": title[:300],
                    "tender_type": categorize(title),
                    "organization": org_name,
                    "location": f"{org_name}, {province}",
                    "closing_date": deadline,
                    "url": url,
                    "status": "open",
                    "category": categorize(title),
                    "raw_text": title,
                })
    return tenders


def categorize(title):
    """Categorize tender from title keywords."""
    t = title.lower()
    if any(w in t for w in ["construction", "build", "renovation", "retrofit", "repair", "paving", "rebar", "concrete"]):
        return "Construction"
    if any(w in t for w in ["consulting", "professional service", "study", "assessment", "management services"]):
        return "Consulting"
    if any(w in t for w in ["supply", "deliver", "equipment", "materials", "hardware", "chemicals"]):
        return "Goods & Supply"
    if any(w in t for w in ["service", "maintenance", "cleaning", "waste", "glass"]):
        return "Services"
    if any(w in t for w in ["design", "architect", "engineering", "survey"]):
        return "Engineering"
    if any(w in t for w in ["it ", "software", "data", "network", "fibre", "colocation", "storage"]):
        return "IT & Technology"
    return "General"


def parse_date_flexible(date_str):
    """Parse various date formats. Returns ISO string or None."""
    if not date_str:
        return None
    clean = re.sub(r'\s*\(.*?\)', '', date_str).strip().rstrip(',')
    formats = [
        "%B %d, %Y, %I:%M %p", "%B %d, %Y %I:%M %p", "%B %d, %Y",
        "%b %d, %Y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(clean, fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return None


def parse_bidsandtenders_date(date_str):
    """Parse dates like 'Wed Mar 18, 2026 2:00:00 PM (PDT)'."""
    if not date_str:
        return None
    clean = re.sub(r'\s*\(.*?\)', '', date_str).strip()
    clean = re.sub(r'^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+', '', clean)
    formats = ["%b %d, %Y %I:%M:%S %p", "%b %d, %Y %I:%M %p", "%b %d, %Y"]
    for fmt in formats:
        try:
            dt = datetime.strptime(clean, fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return None


def get_embedding(text):
    """Get OpenAI embedding for text."""
    if not OPENAI_KEY:
        return None
    url = "https://api.openai.com/v1/embeddings"
    data = json.dumps({"input": text[:8000], "model": "text-embedding-3-small"}).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            return result["data"][0]["embedding"]
    except Exception as e:
        print(f"  Embedding error: {e}", file=sys.stderr)
        return None


def crawl_source(source, dry_run=False):
    """Crawl a single source and return parsed tenders."""
    slug = source["slug"]
    url = source["url"]
    platform = source.get("platform", "")
    name = source["name"]

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Crawling: {name} ({platform})", file=sys.stderr)
    print(f"URL: {url}", file=sys.stderr)

    wait_for = 3000 if platform in ["bidsandtenders", "sciquest", "bonfire"] else None
    markdown = firecrawl_scrape(url, wait_for)
    if not markdown:
        print(f"  FAILED to scrape {name}", file=sys.stderr)
        return []

    province = source.get("province", "BC")

    if platform == "civicinfo":
        tenders = parse_civicinfo(markdown)
    elif platform == "bidsandtenders":
        tenders = parse_bidsandtenders(markdown, name, province)
    elif platform == "bonfire":
        tenders = parse_bonfire(markdown, name, province)
    elif platform == "constructconnect":
        # Aggregator source — paginate to get more results
        tenders = parse_constructconnect(markdown, name)
        # Crawl additional pages (up to 5 pages = ~50 tenders)
        for page in range(2, 6):
            page_url = f"{url}?cctpage={page}"
            page_md = firecrawl_scrape(page_url, wait_for=5000)
            if page_md:
                page_tenders = parse_constructconnect(page_md, name)
                tenders.extend(page_tenders)
                print(f"  Page {page}: {len(page_tenders)} tenders", file=sys.stderr)
            time.sleep(1.5)
    else:
        tenders = parse_generic(markdown, name, province)

    for t in tenders:
        t["source_id"] = source["id"]
        t["province"] = source.get("province", "BC")

    print(f"  Found {len(tenders)} tenders", file=sys.stderr)
    return tenders


def load_tenders(tenders, dry_run=False):
    """Load tenders into Supabase."""
    if not tenders:
        return 0

    loaded = 0
    for t in tenders:
        if dry_run:
            print(f"  [DRY RUN] {t['title'][:70]}", file=sys.stderr)
            loaded += 1
            continue

        clean = {k: v for k, v in t.items() if v is not None and v != ""}
        result = supabase_post("tenders", clean, upsert=True)
        if result:
            loaded += 1
            print(f"  + {t['title'][:65]}", file=sys.stderr)

    return loaded


def embed_missing():
    """Embeddings disabled — using keyword search only."""
    print("Embeddings disabled. Using structured keyword search instead.", file=sys.stderr)


def get_stats_json():
    """Get stats as JSON for the dashboard."""
    sources = supabase_get("procurement_sources?select=id,name,slug,source_type,platform,region,last_crawled_at&order=id")
    tenders = supabase_get("tenders?select=id,title,tender_type,organization,status,source_id,closing_date,category,location,url,posted_date&order=closing_date")

    by_source = {}
    by_type = {}
    by_category = {}
    by_status = {}
    by_region = {}
    open_tenders = []

    source_map = {s["id"]: s for s in sources}

    for t in tenders:
        sid = t.get("source_id")
        src_name = source_map.get(sid, {}).get("name", "Unknown")
        by_source[src_name] = by_source.get(src_name, 0) + 1

        tt = t.get("tender_type") or "Unknown"
        by_type[tt] = by_type.get(tt, 0) + 1

        cat = t.get("category") or "General"
        by_category[cat] = by_category.get(cat, 0) + 1

        st = t.get("status") or "unknown"
        by_status[st] = by_status.get(st, 0) + 1

        region = source_map.get(sid, {}).get("region", "Unknown")
        by_region[region] = by_region.get(region, 0) + 1

        if st == "open":
            open_tenders.append({
                "id": t["id"],
                "title": t.get("title", ""),
                "organization": t.get("organization", ""),
                "tender_type": t.get("tender_type", ""),
                "category": t.get("category", ""),
                "closing_date": t.get("closing_date", ""),
                "location": t.get("location", ""),
                "url": t.get("url", ""),
                "source": src_name,
            })

    stats = {
        "total_tenders": len(tenders),
        "open_tenders": len([t for t in tenders if t.get("status") == "open"]),
        "sources_count": len(sources),
        "unique_organizations": len(set(t.get("organization", "") for t in tenders if t.get("organization"))),
        "by_source": dict(sorted(by_source.items(), key=lambda x: -x[1])),
        "by_type": dict(sorted(by_type.items(), key=lambda x: -x[1])),
        "by_category": dict(sorted(by_category.items(), key=lambda x: -x[1])),
        "by_status": dict(sorted(by_status.items(), key=lambda x: -x[1])),
        "by_region": dict(sorted(by_region.items(), key=lambda x: -x[1])),
        "sources": sources,
        "tenders": open_tenders,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    return stats


def main():
    parser = argparse.ArgumentParser(description="Canadian procurement tender crawler")
    parser.add_argument("--source", help="Crawl specific source by slug")
    parser.add_argument("--province", help="Crawl only sources for a specific province (BC, AB, ON, QC, etc.)")
    parser.add_argument("--embed", action="store_true", help="Generate missing embeddings")
    parser.add_argument("--stats", action="store_true", help="Output stats as JSON")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, don't write to DB")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set", file=sys.stderr)
        sys.exit(1)

    if args.stats:
        print(json.dumps(get_stats_json(), indent=2))
        return

    if args.embed:
        embed_missing()
        return

    if args.source:
        sources = supabase_get(f"procurement_sources?slug=eq.{args.source}&active=is.true")
    elif args.province:
        sources = supabase_get(f"procurement_sources?province=eq.{args.province}&active=is.true&order=id")
    else:
        sources = supabase_get("procurement_sources?active=is.true&order=id")

    if not sources:
        print("No active sources found", file=sys.stderr)
        sys.exit(1)

    print(f"Crawling {len(sources)} sources...", file=sys.stderr)
    total = 0

    for source in sources:
        tenders = crawl_source(source, args.dry_run)
        if tenders:
            loaded = load_tenders(tenders, args.dry_run)
            total += loaded
            if not args.dry_run:
                supabase_patch(
                    "procurement_sources",
                    {"last_crawled_at": datetime.now(timezone.utc).isoformat()},
                    f"id=eq.{source['id']}"
                )
        if source != sources[-1]:
            time.sleep(1.5)

    print(f"\nDONE — Loaded {total} tenders from {len(sources)} sources", file=sys.stderr)
    print(json.dumps(get_stats_json(), indent=2))


if __name__ == "__main__":
    main()
