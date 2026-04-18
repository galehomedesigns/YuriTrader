---
name: youtube
description: Search YouTube and fetch video transcripts for content research and repurposing. Use when Tony asks to find videos, get transcripts, or research content for social media posts.
---

# YouTube Research

Search YouTube and extract transcripts from videos for content repurposing.

## Search Videos

```bash
python3 /home/tonygale/openclaw/skills/youtube/scripts/search.py "project planning AI construction"
```

Options:
- First argument: search query (required)
- `--count N` — Number of results (default: 5)

## Get Transcript

```bash
python3 /home/tonygale/openclaw/skills/youtube/scripts/transcript.py "https://youtube.com/watch?v=VIDEO_ID"
```

Options:
- First argument: YouTube URL or video ID (required)
- `--summary` — Only show first 3000 chars (for token efficiency)

## Use Cases

- Research Tony's own videos for content to repurpose into social posts
- Find competitor/industry videos for commentary
- Extract key quotes and talking points from video content
- Generate social media posts based on video transcripts
