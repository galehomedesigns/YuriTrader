#!/usr/bin/env python3
"""
Financial news RSS scanner — fetches headlines from major outlets.

Usage:
    python3 news_scanner.py fetch       # Fetch new articles from all sources
    python3 news_scanner.py sources     # List configured sources
"""

import hashlib
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

# Free RSS feeds for financial news
RSS_SOURCES = {
    "reuters": {
        "name": "Reuters Business",
        "url": "https://news.google.com/rss/search?q=site:reuters.com+business&hl=en-US&gl=US&ceid=US:en",
    },
    "cnbc": {
        "name": "CNBC Top News",
        "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    },
    "marketwatch": {
        "name": "MarketWatch Top Stories",
        "url": "https://feeds.marketwatch.com/marketwatch/topstories/",
    },
    "yahoo_finance": {
        "name": "Yahoo Finance",
        "url": "https://finance.yahoo.com/news/rssindex",
    },
    "financial_post": {
        "name": "Financial Post",
        "url": "https://financialpost.com/feed",
    },
    "google_bloomberg": {
        "name": "Bloomberg via Google News",
        "url": "https://news.google.com/rss/search?q=site:bloomberg.com+markets&hl=en-US&gl=US&ceid=US:en",
    },
    "google_tariffs": {
        "name": "Tariff/Trade News",
        "url": "https://news.google.com/rss/search?q=tariffs+OR+trade+war+OR+sanctions+stock+market&hl=en-US&gl=US&ceid=US:en",
    },
}

# Market-relevant keywords for filtering
MARKET_KEYWORDS = [
    "tariff", "trade war", "sanctions", "interest rate", "fed ", "federal reserve",
    "inflation", "recession", "gdp", "earnings", "ipo", "merger", "acquisition",
    "stock market", "s&p 500", "nasdaq", "dow jones", "tsx", "oil price",
    "executive order", "policy", "regulation", "bank of canada", "rate cut",
    "rate hike", "jobs report", "unemployment", "cpi", "ppi",
]


def parse_rss(xml_text, source_name):
    """Parse RSS XML and return list of articles."""
    articles = []
    try:
        root = ET.fromstring(xml_text)
        # Handle both RSS 2.0 and Atom formats
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")

        for item in items[:20]:  # Limit to 20 per source
            title = ""
            link = ""
            pub_date = ""
            description = ""

            # RSS 2.0
            t = item.find("title")
            if t is not None and t.text:
                title = t.text.strip()
            l = item.find("link")
            if l is not None and l.text:
                link = l.text.strip()
            elif l is not None and l.get("href"):
                link = l.get("href")
            d = item.find("description")
            if d is not None and d.text:
                description = d.text.strip()[:500]
            p = item.find("pubDate")
            if p is not None and p.text:
                pub_date = p.text.strip()

            # Atom fallback
            if not title:
                t = item.find("{http://www.w3.org/2005/Atom}title")
                if t is not None and t.text:
                    title = t.text.strip()
            if not link:
                l = item.find("{http://www.w3.org/2005/Atom}link")
                if l is not None:
                    link = l.get("href", "")
            if not pub_date:
                p = item.find("{http://www.w3.org/2005/Atom}published") or item.find("{http://www.w3.org/2005/Atom}updated")
                if p is not None and p.text:
                    pub_date = p.text.strip()

            if title and link:
                articles.append({
                    "title": title,
                    "summary": description,
                    "url": link,
                    "source": source_name,
                    "published_at": pub_date or None,
                })
    except ET.ParseError:
        pass

    return articles


def check_market_relevance(title, summary=""):
    """Quick keyword check for market relevance."""
    text = (title + " " + (summary or "")).lower()
    matched = [kw for kw in MARKET_KEYWORDS if kw in text]
    return matched


def cmd_fetch():
    """Fetch new articles from all RSS sources."""
    client = httpx.Client(timeout=15, follow_redirects=True, headers={
        "User-Agent": "Mozilla/5.0 (compatible; OpenClaw/1.0)"
    })

    all_articles = []
    errors = []

    for slug, source in RSS_SOURCES.items():
        try:
            resp = client.get(source["url"])
            if resp.status_code == 200:
                articles = parse_rss(resp.text, slug)
                all_articles.extend(articles)
                print(f"  {source['name']}: {len(articles)} articles")
            else:
                errors.append(f"{source['name']}: HTTP {resp.status_code}")
        except Exception as e:
            errors.append(f"{source['name']}: {str(e)[:80]}")

    client.close()

    if errors:
        print(f"\n  Errors: {len(errors)}")
        for e in errors:
            print(f"    - {e}")

    if not all_articles:
        print("\nNo articles fetched.")
        return

    # Deduplicate by URL and insert new ones
    new_count = 0
    for article in all_articles:
        if not article.get("url"):
            continue

        # Check if already exists
        existing = httpx.get(
            f"{SUPABASE_URL}/rest/v1/news_events",
            headers={**HEADERS, "Prefer": "return=representation"},
            params={"url": f"eq.{article['url']}", "select": "id", "limit": "1"},
            timeout=10,
        )

        if existing.status_code == 200 and existing.json():
            continue  # Already stored

        # Check market relevance
        keywords = check_market_relevance(article["title"], article.get("summary", ""))

        record = {
            "title": article["title"],
            "summary": article.get("summary"),
            "url": article["url"],
            "source": article["source"],
            "published_at": article.get("published_at"),
        }

        try:
            resp = httpx.post(
                f"{SUPABASE_URL}/rest/v1/news_events",
                headers={**HEADERS, "Prefer": "return=minimal"},
                json=record,
                timeout=10,
            )
            if resp.status_code in (200, 201):
                new_count += 1
        except Exception:
            pass

    print(f"\nTotal: {len(all_articles)} articles fetched, {new_count} new stored.")


def cmd_sources():
    """List configured news sources."""
    print("Configured news sources:")
    for slug, source in RSS_SOURCES.items():
        print(f"  [{slug}] {source['name']}: {source['url'][:60]}...")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1].lower()
    if cmd == "fetch":
        cmd_fetch()
    elif cmd == "sources":
        cmd_sources()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
