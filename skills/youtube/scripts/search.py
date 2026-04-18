#!/usr/bin/env python3
"""Search YouTube videos using yt-dlp."""

import argparse
import json
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description="Search YouTube")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--count", type=int, default=5, help="Number of results")
    args = parser.parse_args()

    cmd = [
        "yt-dlp", "--flat-playlist", "--dump-json",
        "--playlist-end", str(args.count),
        f"ytsearch{args.count}:{args.query}"
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    videos = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        data = json.loads(line)
        videos.append({
            "title": data.get("title", ""),
            "url": data.get("url", data.get("webpage_url", "")),
            "channel": data.get("channel", data.get("uploader", "")),
            "duration": data.get("duration_string", ""),
            "view_count": data.get("view_count", 0),
            "description": (data.get("description", "") or "")[:300],
        })

    print(json.dumps(videos, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
