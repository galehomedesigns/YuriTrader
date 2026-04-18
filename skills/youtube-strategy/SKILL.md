---
name: youtube-strategy
description: Scrape YouTube channels for trading content, transcribe videos, and analyze for trading strategies. Use when asked to scan YouTubers, transcribe trading channels, or extract strategies.
---

# YouTube Strategy Analyzer

Scrape trading YouTubers, transcribe their videos, and extract actionable trading strategies.

## Scripts

### Channel Scanner
```bash
python3 /data/skills/youtube-strategy/scripts/channel_scanner.py <channel_url> [--resume] [--limit N]
```
Enumerates all videos from a YouTube channel and stores metadata in Supabase `yt_videos` table.

### Transcript Fetcher
```bash
python3 /data/skills/youtube-strategy/scripts/transcript_fetcher.py [--batch 20] [--channel CHANNEL_ID]
```
Transcribes pending videos using youtube-transcript-api (fast) with yt-dlp fallback.

### Strategy Analyzer
```bash
python3 /data/skills/youtube-strategy/scripts/strategy_analyzer.py [--batch 10] [--channel CHANNEL_ID]
```
Sends transcripts to LLM to extract trading strategies (entry/exit rules, indicators, risk management).

### Report Generator
```bash
python3 /data/skills/youtube-strategy/scripts/report_generator.py <channel_id> [--format markdown|html]
```
Compiles extracted strategies into a ranked report, deduplicates across videos.

## Database Tables

- `yt_channels` — Channel metadata and scan status
- `yt_videos` — Video metadata, transcripts, and processing status
- `yt_strategies` — Extracted strategies with indicators, rules, confidence scores

## Environment Variables

- `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` — Database access
- `OLLAMA_BASE_URL` — LLM for strategy analysis
