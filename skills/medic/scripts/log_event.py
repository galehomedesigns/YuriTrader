#!/usr/bin/env python3
"""
Log events directly to Supabase conversation_log with auto-embedding.
Replaces the fragile agent-mediated memory sync.

Usage:
    python3 log_event.py --source "trading-premarket" --summary "Briefing: AAPL +1.2%" --topics trading alerts
    python3 log_event.py --backfill-today       # Sync unlogged memory files from today
    python3 log_event.py --backfill-range 2026-03-01 2026-03-27  # Backfill date range
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
MEMORY_DIR = Path("/data/.openclaw/workspace/memory")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def get_embedding(text):
    """Generate a 1024-dim embedding via local Ollama mxbai-embed-large."""
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    try:
        resp = httpx.post(
            f"{ollama_url}/api/embed",
            headers={"Content-Type": "application/json"},
            json={"model": "mxbai-embed-large", "input": text[:1500]},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()["embeddings"][0]
    except Exception as e:
        print(f"  Embedding failed: {e}")
        return None


def log_to_supabase(source, summary, topics=None, session_date=None):
    """Write a conversation_log entry with embedding."""
    if not summary or not summary.strip():
        return False

    date = session_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tagged_summary = f"[{source}] {summary}"

    embedding = get_embedding(tagged_summary)

    record = {
        "session_date": date,
        "summary": tagged_summary,
        "topics": topics or [],
        "decisions": [],
        "next_steps": [],
        "files_changed": [],
        "skills_used": [source],
    }

    if embedding:
        record["embedding"] = embedding

    try:
        resp = httpx.post(
            f"{SUPABASE_URL}/rest/v1/conversation_log",
            headers={**HEADERS, "Prefer": "return=minimal"},
            json=record,
            timeout=15,
        )
        if resp.status_code in (200, 201):
            print(f"  Logged: [{source}] {summary[:80]}...")
            return True
        else:
            print(f"  Failed ({resp.status_code}): {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"  Error: {e}")
        return False


def check_date_logged(date_str):
    """Check if a date already has a conversation_log entry."""
    try:
        resp = httpx.get(
            f"{SUPABASE_URL}/rest/v1/conversation_log",
            headers={**HEADERS, "Prefer": "return=representation"},
            params={"session_date": f"eq.{date_str}", "select": "id", "limit": "1"},
            timeout=10,
        )
        return resp.status_code == 200 and len(resp.json()) > 0
    except Exception:
        return False


def backfill_date(date_str):
    """Read memory file for a date and log it if not already in DB."""
    if check_date_logged(date_str):
        print(f"  {date_str}: already synced, skipping.")
        return False

    mem_file = MEMORY_DIR / f"{date_str}.md"
    if not mem_file.exists():
        return False

    content = mem_file.read_text().strip()
    if not content or len(content) < 20:
        return False

    # Use first 2000 chars as summary
    summary = content[:2000]
    # Extract topics from headers
    topics = []
    for line in content.split("\n"):
        if line.startswith("## "):
            topics.append(line[3:].strip().lower().replace(" ", "-"))

    return log_to_supabase("memory-file", summary, topics=topics[:10], session_date=date_str)


def cmd_log(args):
    """Log a single event."""
    topics = args.topics.split() if args.topics else []
    log_to_supabase(args.source, args.summary, topics=topics)


def cmd_backfill_today():
    """Backfill today's memory file if not synced."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"Backfilling today ({today})...")
    if backfill_date(today):
        print(f"  Synced {today}.")
    else:
        print(f"  Nothing to sync for {today}.")


def cmd_backfill_range(start, end):
    """Backfill a range of dates."""
    from datetime import timedelta
    current = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    synced = 0
    skipped = 0

    print(f"Backfilling {start} to {end}...")
    while current <= end_dt:
        date_str = current.strftime("%Y-%m-%d")
        if backfill_date(date_str):
            synced += 1
        else:
            skipped += 1
        current += timedelta(days=1)

    print(f"Done: {synced} synced, {skipped} skipped.")


def main():
    parser = argparse.ArgumentParser(description="Log events to Supabase conversation_log")
    parser.add_argument("--source", help="Event source (e.g., trading-premarket)")
    parser.add_argument("--summary", help="Event summary text")
    parser.add_argument("--topics", help="Space-separated topic tags")
    parser.add_argument("--backfill-today", action="store_true", help="Sync today's unlogged memory file")
    parser.add_argument("--backfill-range", nargs=2, metavar=("START", "END"), help="Backfill date range YYYY-MM-DD")

    args = parser.parse_args()

    if args.backfill_today:
        cmd_backfill_today()
    elif args.backfill_range:
        cmd_backfill_range(args.backfill_range[0], args.backfill_range[1])
    elif args.source and args.summary:
        cmd_log(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
