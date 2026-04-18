#!/usr/bin/env python3
"""Fetch YouTube video transcript using yt-dlp."""

import argparse
import json
import subprocess
import sys
import os
import glob


def main():
    parser = argparse.ArgumentParser(description="Get YouTube transcript")
    parser.add_argument("url", help="YouTube URL or video ID")
    parser.add_argument("--summary", action="store_true", help="Truncate to 3000 chars")
    args = parser.parse_args()

    # Get video info
    info_cmd = ["yt-dlp", "--dump-json", "--skip-download", args.url]
    info_result = subprocess.run(info_cmd, capture_output=True, text=True)
    if info_result.returncode != 0:
        print(f"Error getting video info: {info_result.stderr}", file=sys.stderr)
        sys.exit(1)

    info = json.loads(info_result.stdout)
    print(f"Title: {info.get('title', 'Unknown')}")
    print(f"Channel: {info.get('channel', info.get('uploader', 'Unknown'))}")
    print(f"Duration: {info.get('duration_string', 'Unknown')}")
    print(f"URL: {info.get('webpage_url', args.url)}")
    print("---")

    # Try to get subtitles
    tmp_dir = "/tmp/yt-transcript"
    os.makedirs(tmp_dir, exist_ok=True)

    sub_cmd = [
        "yt-dlp", "--skip-download",
        "--write-auto-sub", "--write-sub",
        "--sub-lang", "en",
        "--sub-format", "vtt",
        "--convert-subs", "srt",
        "-o", f"{tmp_dir}/%(id)s.%(ext)s",
        args.url
    ]
    subprocess.run(sub_cmd, capture_output=True, text=True)

    # Find the subtitle file
    video_id = info.get("id", "")
    sub_files = glob.glob(f"{tmp_dir}/{video_id}*.srt")

    if not sub_files:
        # Fallback: use description
        desc = info.get("description", "No transcript available.")
        print(f"No transcript found. Video description:\n{desc}")
        return

    with open(sub_files[0], "r") as f:
        lines = f.readlines()

    # Parse SRT - extract text lines only
    text_lines = []
    for line in lines:
        line = line.strip()
        if not line or line.isdigit() or "-->" in line:
            continue
        if line not in text_lines[-1:]:  # deduplicate consecutive
            text_lines.append(line)

    transcript = " ".join(text_lines)

    if args.summary and len(transcript) > 3000:
        transcript = transcript[:3000] + "\n... (truncated, use without --summary for full)"

    print(f"Transcript:\n{transcript}")

    # Cleanup
    for f in sub_files:
        os.remove(f)


if __name__ == "__main__":
    main()
