#!/usr/bin/env python3
"""Enumerate all videos from a YouTube channel and store metadata in Supabase."""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation,resolution=merge-duplicates",
}


def supabase_upsert(table, data, on_conflict=None):
    """Upsert records to Supabase via REST API."""
    import httpx
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    if on_conflict:
        url += f"?on_conflict={on_conflict}"
    body = json.dumps(data if isinstance(data, list) else [data])
    r = httpx.post(url, headers=HEADERS, content=body, timeout=30)
    if r.status_code >= 400:
        print(f"  Supabase error ({table}): {r.status_code} {r.text[:200]}", file=sys.stderr)
        return None
    return r.json()


def get_existing_video_ids(channel_id):
    """Get set of video_ids already in Supabase for this channel."""
    import httpx
    ids = set()
    url = f"{SUPABASE_URL}/rest/v1/yt_videos?channel_id=eq.{channel_id}&select=video_id"
    headers = {k: v for k, v in HEADERS.items() if k != "Prefer"}
    r = httpx.get(url, headers=headers, timeout=30)
    if r.status_code == 200:
        ids = {row["video_id"] for row in r.json()}
    return ids


def scan_channel(channel_url, resume=False, limit=None):
    """Scan a YouTube channel and store video metadata."""
    # Normalize URL to /videos tab
    base_url = channel_url.rstrip("/")
    if not base_url.endswith("/videos"):
        base_url += "/videos"

    # Get channel info via flat-playlist (avoids bot detection)
    print(f"Scanning channel: {base_url}", file=sys.stderr)
    info_cmd = ["yt-dlp", "--js-runtimes", "node", "--flat-playlist", "--dump-json",
                "--playlist-end", "1", base_url]
    info_result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=120)
    if info_result.returncode != 0:
        print(f"Error getting channel info: {info_result.stderr[:300]}", file=sys.stderr)
        sys.exit(1)

    first_entry = json.loads(info_result.stdout.strip().split("\n")[0])
    # flat-playlist puts channel info in different fields
    channel_id = (first_entry.get("channel_id") or first_entry.get("uploader_id")
                  or first_entry.get("playlist_channel_id") or "unknown")
    channel_name = (first_entry.get("channel") or first_entry.get("uploader")
                    or first_entry.get("playlist_channel") or "Unknown")
    # If still unknown, derive from URL
    if channel_id == "unknown":
        # Extract from URL like @TradingwithRayner
        import re
        m = re.search(r"@([\w.-]+)", channel_url)
        channel_id = m.group(1) if m else "unknown"
    if channel_name == "Unknown" and "@" in channel_url:
        import re
        m = re.search(r"@([\w.-]+)", channel_url)
        channel_name = m.group(1) if m else "Unknown"

    # Upsert channel record
    channel_data = {
        "channel_id": channel_id,
        "channel_name": channel_name,
        "channel_url": channel_url,
        "scan_status": "scanning",
        "last_scanned_at": datetime.now(timezone.utc).isoformat(),
    }
    supabase_upsert("yt_channels", channel_data, on_conflict="channel_id")
    print(f"Channel: {channel_name} ({channel_id})", file=sys.stderr)

    # Get existing video IDs if resuming
    existing_ids = set()
    if resume:
        existing_ids = get_existing_video_ids(channel_id)
        print(f"Resume mode: {len(existing_ids)} videos already in database", file=sys.stderr)

    # Enumerate all videos
    cmd = ["yt-dlp", "--js-runtimes", "node", "--flat-playlist", "--dump-json"]
    if limit:
        cmd.extend(["--playlist-end", str(limit)])
    cmd.append(base_url)

    print("Enumerating videos...", file=sys.stderr)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    batch = []
    total = 0
    skipped = 0
    stored = 0

    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        total += 1
        vid = data.get("id", "")

        if resume and vid in existing_ids:
            skipped += 1
            if total % 100 == 0:
                print(f"  Progress: {total} scanned, {stored} new, {skipped} skipped", file=sys.stderr)
            continue

        video_record = {
            "video_id": vid,
            "channel_id": channel_id,
            "title": data.get("title", "Untitled"),
            "url": f"https://www.youtube.com/watch?v={vid}",
            "duration_seconds": int(data["duration"]) if data.get("duration") else None,
            "upload_date": data.get("upload_date"),
            "view_count": data.get("view_count"),
            "description": (data.get("description") or "")[:2000],
        }
        batch.append(video_record)

        if len(batch) >= 50:
            supabase_upsert("yt_videos", batch, on_conflict="video_id")
            stored += len(batch)
            print(f"  Progress: {total} scanned, {stored} stored, {skipped} skipped", file=sys.stderr)
            batch = []
            time.sleep(0.5)

    proc.wait()

    # Store remaining batch
    if batch:
        supabase_upsert("yt_videos", batch)
        stored += len(batch)

    # Update channel record
    channel_data["scan_status"] = "complete"
    channel_data["video_count"] = total
    supabase_upsert("yt_channels", channel_data, on_conflict="channel_id")

    result = {
        "channel_id": channel_id,
        "channel_name": channel_name,
        "total_videos": total,
        "new_stored": stored,
        "skipped": skipped,
        "status": "complete",
    }
    print(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description="Scan YouTube channel videos")
    parser.add_argument("channel_url", help="YouTube channel URL")
    parser.add_argument("--resume", action="store_true", help="Skip already-scanned videos")
    parser.add_argument("--limit", type=int, help="Max videos to scan")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_KEY required", file=sys.stderr)
        sys.exit(1)

    scan_channel(args.channel_url, resume=args.resume, limit=args.limit)


if __name__ == "__main__":
    main()
