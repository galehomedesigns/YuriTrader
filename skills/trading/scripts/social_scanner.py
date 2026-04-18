#!/usr/bin/env python3
"""
Social media scanner — monitors Truth Social and political news for market-moving signals.

Usage:
    python3 social_scanner.py truth-social    # Fetch recent Trump posts
    python3 social_scanner.py news-headlines  # Fetch political/policy headlines
    python3 social_scanner.py check-new       # Only items since last check
"""

import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
FIRECRAWL_API_KEY = os.environ.get("FIRECRAWL_API_KEY", "")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

# Market-relevant keywords for filtering Trump posts
MARKET_KEYWORDS = [
    "tariff", "trade", "china", "canada", "mexico", "eu ", "european union",
    "tax", "interest rate", "fed ", "federal reserve", "stock", "market",
    "oil", "gas", "energy", "sanction", "ban", "executive order",
    "deal", "agreement", "negotiate", "billion", "trillion",
    "jobs", "employment", "economy", "gdp", "inflation", "recession",
    "crypto", "bitcoin", "regulation", "deregulation", "border",
    "auto", "steel", "aluminum", "semiconductor", "chip", "tech",
]

# Political/policy news RSS feeds
POLITICAL_FEEDS = {
    "google_trump_policy": {
        "name": "Trump Policy News",
        "url": "https://news.google.com/rss/search?q=trump+executive+order+OR+tariff+OR+trade+policy+OR+sanctions&hl=en-US&gl=US&ceid=US:en",
    },
    "google_us_economy": {
        "name": "US Economy Policy",
        "url": "https://news.google.com/rss/search?q=federal+reserve+OR+interest+rate+OR+treasury+policy&hl=en-US&gl=US&ceid=US:en",
    },
    "google_canada_trade": {
        "name": "Canada Trade News",
        "url": "https://news.google.com/rss/search?q=canada+trade+tariff+OR+canada+us+trade&hl=en-CA&gl=CA&ceid=CA:en",
    },
}


def content_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def check_relevance(text):
    """Check if text contains market-relevant keywords."""
    lower = text.lower()
    matched = [kw for kw in MARKET_KEYWORDS if kw in lower]
    if len(matched) >= 3:
        return "HIGH", matched
    elif len(matched) >= 1:
        return "MEDIUM", matched
    return "LOW", matched


def store_signal(platform, author, content, severity, keywords, posted_at=None):
    """Store a social signal in Supabase, deduplicating by content hash."""
    chash = content_hash(content)

    # Check if already exists
    existing = httpx.get(
        f"{SUPABASE_URL}/rest/v1/social_signals",
        headers={**HEADERS, "Prefer": "return=representation"},
        params={"content_hash": f"eq.{chash}", "select": "id", "limit": "1"},
        timeout=10,
    )
    if existing.status_code == 200 and existing.json():
        return False  # Duplicate

    record = {
        "platform": platform,
        "author": author,
        "content": content[:2000],
        "content_hash": chash,
        "market_relevant": severity in ("HIGH", "MEDIUM"),
        "severity": severity,
        "keywords": keywords[:10],
        "posted_at": posted_at,
    }

    resp = httpx.post(
        f"{SUPABASE_URL}/rest/v1/social_signals",
        headers={**HEADERS, "Prefer": "return=minimal"},
        json=record,
        timeout=10,
    )
    return resp.status_code in (200, 201)


def cmd_truth_social():
    """Fetch Trump posts from Truth Social via multiple fallback methods."""
    print("Scanning Truth Social (@realDonaldTrump)...")

    posts = []

    # Method 1: Try Firecrawl scrape
    posts = _try_firecrawl_truth_social()

    # Method 2: If Firecrawl failed, try Google News for Trump statements
    if not posts:
        print("  Firecrawl unavailable, falling back to news aggregation...")
        posts = _try_news_aggregation_trump()

    if not posts:
        print("  No posts found from any source.")
        return

    # Filter and store
    new_count = 0
    high_signals = []

    for post in posts:
        severity, keywords = check_relevance(post["content"])
        stored = store_signal(
            platform="truth_social",
            author="@realDonaldTrump",
            content=post["content"],
            severity=severity,
            keywords=keywords,
            posted_at=post.get("posted_at"),
        )
        if stored:
            new_count += 1
            if severity == "HIGH":
                high_signals.append(post["content"][:200])

    print(f"  Processed {len(posts)} posts, {new_count} new stored.")

    if high_signals:
        print(f"\n  HIGH SEVERITY SIGNALS ({len(high_signals)}):")
        for sig in high_signals:
            print(f"    - {sig}")


def _try_firecrawl_truth_social():
    """Try scraping Truth Social via Firecrawl API."""
    try:
        resp = httpx.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={
                "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "url": "https://truthsocial.com/@realDonaldTrump",
                "formats": ["markdown"],
                "waitFor": 5000,
            },
            timeout=30,
        )

        if resp.status_code != 200:
            print(f"  Firecrawl returned {resp.status_code}")
            return []

        data = resp.json()
        markdown = data.get("data", {}).get("markdown", "")

        if not markdown or len(markdown) < 100:
            print("  Firecrawl returned empty content.")
            return []

        # Parse posts from markdown — Truth Social posts are typically
        # separated by timestamps or dividers
        posts = []
        current_post = []
        for line in markdown.split("\n"):
            line = line.strip()
            if not line:
                if current_post:
                    text = " ".join(current_post)
                    if len(text) > 20:  # Skip short fragments
                        posts.append({"content": text})
                    current_post = []
            else:
                current_post.append(line)

        if current_post:
            text = " ".join(current_post)
            if len(text) > 20:
                posts.append({"content": text})

        print(f"  Firecrawl: {len(posts)} posts parsed.")
        return posts[:15]

    except Exception as e:
        print(f"  Firecrawl error: {str(e)[:80]}")
        return []


def _try_news_aggregation_trump():
    """Fallback: fetch Trump statement news from Google News RSS."""
    try:
        client = httpx.Client(timeout=15, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (compatible; OpenClaw/1.0)"
        })

        url = "https://news.google.com/rss/search?q=trump+statement+OR+trump+announces+OR+trump+truth+social&hl=en-US&gl=US&ceid=US:en"
        resp = client.get(url)
        client.close()

        if resp.status_code != 200:
            return []

        root = ET.fromstring(resp.text)
        items = root.findall(".//item")

        posts = []
        for item in items[:15]:
            title = item.find("title")
            pub = item.find("pubDate")
            if title is not None and title.text:
                posts.append({
                    "content": title.text.strip(),
                    "posted_at": pub.text.strip() if pub is not None and pub.text else None,
                })

        print(f"  News aggregation: {len(posts)} headlines.")
        return posts

    except Exception as e:
        print(f"  News aggregation error: {str(e)[:80]}")
        return []


def cmd_news_headlines():
    """Fetch political/policy headlines from RSS feeds."""
    print("Scanning political/policy news...")

    client = httpx.Client(timeout=15, follow_redirects=True, headers={
        "User-Agent": "Mozilla/5.0 (compatible; OpenClaw/1.0)"
    })

    new_count = 0
    high_signals = []

    for slug, source in POLITICAL_FEEDS.items():
        try:
            resp = client.get(source["url"])
            if resp.status_code != 200:
                print(f"  {source['name']}: HTTP {resp.status_code}")
                continue

            root = ET.fromstring(resp.text)
            items = root.findall(".//item")

            source_count = 0
            for item in items[:15]:
                title = item.find("title")
                pub = item.find("pubDate")

                if title is None or not title.text:
                    continue

                text = title.text.strip()
                severity, keywords = check_relevance(text)

                stored = store_signal(
                    platform="news",
                    author=source["name"],
                    content=text,
                    severity=severity,
                    keywords=keywords,
                    posted_at=pub.text.strip() if pub is not None and pub.text else None,
                )
                if stored:
                    source_count += 1
                    new_count += 1
                    if severity == "HIGH":
                        high_signals.append(text[:200])

            print(f"  {source['name']}: {source_count} new stored")

        except Exception as e:
            print(f"  {source['name']}: error — {str(e)[:80]}")

    client.close()
    print(f"\nTotal: {new_count} new signals stored.")

    if high_signals:
        print(f"\nHIGH SEVERITY ({len(high_signals)}):")
        for sig in high_signals:
            print(f"  - {sig}")


def cmd_check_new():
    """Return signals stored since last check."""
    # Get last check timestamp
    resp = httpx.get(
        f"{SUPABASE_URL}/rest/v1/trading_config",
        headers={**HEADERS, "Prefer": "return=representation"},
        params={"key": "eq.last_social_check", "select": "value"},
        timeout=10,
    )

    last_check = "2026-03-26T00:00:00Z"
    if resp.status_code == 200 and resp.json():
        last_check = resp.json()[0]["value"].strip('"')

    # Fetch new signals
    signals = httpx.get(
        f"{SUPABASE_URL}/rest/v1/social_signals",
        headers={**HEADERS, "Prefer": "return=representation"},
        params={
            "fetched_at": f"gt.{last_check}",
            "market_relevant": "eq.true",
            "select": "*",
            "order": "fetched_at.desc",
            "limit": "20",
        },
        timeout=10,
    )

    if signals.status_code == 200:
        rows = signals.json()
        if rows:
            print(f"New signals since last check ({len(rows)}):")
            for r in rows:
                print(f"  [{r['severity']}] {r['platform']}: {r['content'][:120]}")
        else:
            print("No new market-relevant signals since last check.")

    # Update last check
    httpx.patch(
        f"{SUPABASE_URL}/rest/v1/trading_config",
        headers={**HEADERS, "Prefer": "return=minimal"},
        params={"key": "eq.last_social_check"},
        json={"value": json.dumps(datetime.utcnow().isoformat()), "updated_at": datetime.utcnow().isoformat()},
        timeout=10,
    )


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1].lower()
    if cmd == "truth-social":
        cmd_truth_social()
    elif cmd == "news-headlines":
        cmd_news_headlines()
    elif cmd == "check-new":
        cmd_check_new()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
