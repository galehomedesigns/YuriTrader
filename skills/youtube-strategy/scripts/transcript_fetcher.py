#!/usr/bin/env python3
"""Batch transcribe YouTube videos using youtube-transcript-api with yt-dlp fallback."""

import argparse
import glob
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def supabase_get(table, params):
    """GET from Supabase REST API."""
    url = f"{SUPABASE_URL}/rest/v1/{table}?{params}"
    r = httpx.get(url, headers=HEADERS, timeout=30)
    if r.status_code >= 400:
        print(f"  Supabase GET error: {r.status_code} {r.text[:200]}", file=sys.stderr)
        return []
    return r.json()


def supabase_patch(table, match_col, match_val, data):
    """PATCH a record in Supabase."""
    url = f"{SUPABASE_URL}/rest/v1/{table}?{match_col}=eq.{match_val}"
    h = {**HEADERS, "Prefer": "return=representation"}
    r = httpx.patch(url, headers=h, content=json.dumps(data), timeout=30)
    if r.status_code >= 400:
        print(f"  Supabase PATCH error: {r.status_code} {r.text[:200]}", file=sys.stderr)
        return None
    return r.json()


def try_transcript_api(video_id):
    """Fetch transcript via GX10 proxy (residential IP, avoids YouTube blocks)."""
    gx10_url = os.environ.get("OLLAMA_BASE_URL", "http://100.84.217.85:11434")
    # Derive GX10 host from Ollama URL
    from urllib.parse import urlparse
    gx10_host = urlparse(gx10_url).hostname or "100.84.217.85"
    proxy_url = f"http://{gx10_host}:8765/?v={video_id}"
    try:
        r = httpx.get(proxy_url, timeout=60)
        if r.status_code == 200:
            data = r.json()
            return data.get("text", ""), "youtube-transcript-api"
        else:
            error = r.json().get("error", f"HTTP {r.status_code}")
            return None, error
    except Exception as e:
        return None, str(e)


def try_ytdlp(video_id):
    """Fallback: use yt-dlp to download auto-subs and parse SRT."""
    tmp_dir = "/tmp/yt-transcript"
    os.makedirs(tmp_dir, exist_ok=True)

    # Clean previous files for this video
    for f in glob.glob(f"{tmp_dir}/{video_id}*"):
        os.remove(f)

    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = [
        "yt-dlp", "--js-runtimes", "node",
        "--skip-download",
        "--write-auto-sub", "--write-sub",
        "--sub-lang", "en",
        "--sub-format", "vtt",
        "--convert-subs", "srt",
        "-o", f"{tmp_dir}/%(id)s.%(ext)s",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return None, "yt-dlp timeout"
    except Exception as e:
        return None, f"yt-dlp error: {str(e)}"

    sub_files = glob.glob(f"{tmp_dir}/{video_id}*.srt")
    if not sub_files:
        return None, "no subtitles found"

    with open(sub_files[0], "r") as f:
        lines = f.readlines()

    # Parse SRT — extract text lines only (reused from existing transcript.py)
    text_lines = []
    for line in lines:
        line = line.strip()
        if not line or line.isdigit() or "-->" in line:
            continue
        # Strip HTML tags from auto-subs
        import re
        line = re.sub(r"<[^>]+>", "", line)
        if line and line not in text_lines[-1:]:
            text_lines.append(line)

    # Cleanup
    for f in sub_files:
        os.remove(f)

    if not text_lines:
        return None, "empty subtitle file"

    return " ".join(text_lines), "yt-dlp"


def fetch_transcripts(batch_size=20, channel_id=None):
    """Fetch and store transcripts for pending videos."""
    # Query pending videos, prioritize by view count
    params = "transcript_status=eq.pending&select=video_id,title,view_count,duration_seconds"
    params += "&order=view_count.desc.nullslast"
    params += f"&limit={batch_size}"
    if channel_id:
        params += f"&channel_id=eq.{channel_id}"

    videos = supabase_get("yt_videos", params)
    if not videos:
        print("No pending videos to transcribe.")
        return {"transcribed": 0, "failed": 0, "skipped": 0}

    print(f"Processing {len(videos)} videos...", file=sys.stderr)

    transcribed = 0
    failed = 0
    skipped = 0

    for i, video in enumerate(videos):
        vid = video["video_id"]
        title = video.get("title", "Unknown")[:60]
        duration = video.get("duration_seconds") or 0

        # Skip very short videos (< 60 seconds — likely intros/shorts)
        if duration > 0 and duration < 60:
            print(f"  [{i+1}/{len(videos)}] Skipping short video ({duration}s): {title}", file=sys.stderr)
            supabase_patch("yt_videos", "video_id", vid, {
                "transcript_status": "skipped",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            skipped += 1
            continue

        print(f"  [{i+1}/{len(videos)}] {title}...", file=sys.stderr, end=" ")

        # Tier 1: youtube-transcript-api
        text, method = try_transcript_api(vid)

        # Tier 2: yt-dlp fallback
        if text is None:
            print("(trying yt-dlp)...", file=sys.stderr, end=" ")
            text, method = try_ytdlp(vid)

        if text:
            supabase_patch("yt_videos", "video_id", vid, {
                "transcript": text,
                "transcript_status": "transcribed",
                "transcript_method": method,
                "transcript_length": len(text),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            print(f"OK ({method}, {len(text)} chars)", file=sys.stderr)
            transcribed += 1
        else:
            supabase_patch("yt_videos", "video_id", vid, {
                "transcript_status": "no_captions",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            print(f"FAIL ({method})", file=sys.stderr)
            failed += 1

        # Small delay between videos
        time.sleep(0.5)

    result = {
        "transcribed": transcribed,
        "failed": failed,
        "skipped": skipped,
        "total_processed": len(videos),
    }
    print(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description="Batch transcribe YouTube videos")
    parser.add_argument("--batch", type=int, default=20, help="Batch size (default 20)")
    parser.add_argument("--channel", type=str, help="Filter by channel_id")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_KEY required", file=sys.stderr)
        sys.exit(1)

    fetch_transcripts(batch_size=args.batch, channel_id=args.channel)


if __name__ == "__main__":
    main()
