#!/usr/bin/env python3
"""Analyze video transcripts with LLM to extract trading strategies."""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = "quality:latest"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

EXTRACTION_PROMPT_TEMPLATE = """You are a trading strategy extraction expert. Analyze this video transcript and extract any actionable trading strategies discussed.

For each strategy found, return valid JSON with this structure:
{{"strategies": [{{"strategy_name": "descriptive name", "strategy_type": "scalping|swing|position|momentum|price_action|mean_reversion|trend_following|breakout", "timeframe": "1m|5m|15m|1h|4h|daily|weekly|multiple", "indicators": ["RSI", "MACD", "EMA 20"], "entry_rules": "specific entry conditions", "exit_rules": "specific exit conditions", "stop_loss_rules": "stop loss placement rules", "risk_management": "position sizing, risk per trade, etc.", "backtested_results": "any results or win rates mentioned", "key_quotes": "1-2 important direct quotes from the video", "confidence_score": 4, "markets": "stocks|forex|crypto|futures|multiple", "summary": "2-3 sentence summary of the strategy"}}]}}

confidence_score: 1=vague mention, 2=discussed but incomplete, 3=clear strategy but missing details, 4=well-defined with rules, 5=fully detailed with backtest results.

If the video contains NO actionable trading strategy (vlog, Q&A, motivational, generic advice), return: {{"strategies": []}}

IMPORTANT: Return ONLY valid JSON, no markdown, no explanation.

Video title: {title}
Transcript:
---
{transcript}
---"""


def supabase_get(table, params):
    url = f"{SUPABASE_URL}/rest/v1/{table}?{params}"
    r = httpx.get(url, headers=HEADERS, timeout=30)
    if r.status_code >= 400:
        return []
    return r.json()


def supabase_patch(table, match_col, match_val, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}?{match_col}=eq.{match_val}"
    h = {**HEADERS, "Prefer": "return=representation"}
    r = httpx.patch(url, headers=h, content=json.dumps(data), timeout=30)
    return r.json() if r.status_code < 400 else None


def supabase_post(table, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    h = {**HEADERS, "Prefer": "return=representation"}
    body = json.dumps(data if isinstance(data, list) else [data])
    r = httpx.post(url, headers=h, content=body, timeout=30)
    if r.status_code >= 400:
        print(f"  Supabase POST error: {r.status_code} {r.text[:200]}", file=sys.stderr)
        return None
    return r.json()


GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash"


def call_llm(prompt, timeout=120):
    """Call Gemini API for fast strategy extraction. Falls back to Ollama."""
    # Try Gemini first (much faster for large text analysis)
    if GEMINI_KEY:
        try:
            r = httpx.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}",
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096}},
                timeout=timeout,
            )
            if r.status_code == 200:
                data = r.json()
                parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                return parts[0].get("text", "") if parts else None
            print(f"  Gemini error: {r.status_code}", file=sys.stderr)
        except Exception as e:
            print(f"  Gemini error: {e}", file=sys.stderr)

    # Fallback to Ollama
    try:
        r = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                   "options": {"temperature": 0.1, "num_predict": 4096, "num_ctx": 32768}},
            timeout=300,
        )
        if r.status_code == 200:
            return r.json().get("response", "")
        print(f"  Ollama error: {r.status_code}", file=sys.stderr)
        return None
    except httpx.TimeoutException:
        print("  LLM timeout", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  LLM error: {e}", file=sys.stderr)
        return None


def parse_strategies(response_text):
    """Parse LLM response into strategy list."""
    if not response_text:
        return []
    # Try to extract JSON from response
    text = response_text.strip()
    # Remove markdown code fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    if text.startswith("json"):
        text = text[4:]
    text = text.strip()

    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        return data.get("strategies", [])
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end])
                if isinstance(data, list):
                    return data
                return data.get("strategies", [])
            except json.JSONDecodeError:
                pass
        # Try finding a JSON array
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end])
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass
    return []


def chunk_transcript(transcript, max_chars=25000, overlap=2000):
    """Split long transcripts into overlapping chunks."""
    if len(transcript) <= max_chars:
        return [transcript]
    chunks = []
    start = 0
    while start < len(transcript):
        end = start + max_chars
        chunks.append(transcript[start:end])
        start = end - overlap
    return chunks


def analyze_videos(batch_size=10, channel_id=None):
    """Analyze transcribed videos for trading strategies."""
    params = ("transcript_status=eq.transcribed&analysis_status=eq.pending"
              "&select=video_id,channel_id,title,transcript,transcript_length,duration_seconds"
              "&order=view_count.desc.nullslast"
              f"&limit={batch_size}")
    if channel_id:
        params += f"&channel_id=eq.{channel_id}"

    videos = supabase_get("yt_videos", params)
    if not videos:
        print("No videos pending analysis.")
        return {"analyzed": 0, "strategies_found": 0, "skipped": 0}

    print(f"Analyzing {len(videos)} videos...", file=sys.stderr)

    analyzed = 0
    strategies_found = 0
    skipped = 0

    for i, video in enumerate(videos):
        vid = video["video_id"]
        cid = video.get("channel_id", "")
        title = video.get("title", "Unknown")[:60]
        transcript = video.get("transcript", "")
        duration = video.get("duration_seconds") or 0

        # Skip very short videos
        if duration > 0 and duration < 120:
            print(f"  [{i+1}/{len(videos)}] Skipping short video ({duration}s): {title}", file=sys.stderr)
            supabase_patch("yt_videos", "video_id", vid, {
                "analysis_status": "skipped",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            skipped += 1
            continue

        if not transcript or len(transcript) < 100:
            supabase_patch("yt_videos", "video_id", vid, {
                "analysis_status": "skipped",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            skipped += 1
            continue

        print(f"  [{i+1}/{len(videos)}] {title}...", file=sys.stderr, end=" ")

        # Chunk if needed
        chunks = chunk_transcript(transcript)
        all_strategies = []

        for ci, chunk in enumerate(chunks):
            if len(chunks) > 1:
                print(f"(chunk {ci+1}/{len(chunks)})", file=sys.stderr, end=" ")
            prompt = EXTRACTION_PROMPT_TEMPLATE.format(title=video.get("title", ""), transcript=chunk)
            response = call_llm(prompt)
            strategies = parse_strategies(response)
            all_strategies.extend(strategies)

        # Store strategies
        if all_strategies:
            for s in all_strategies:
                record = {
                    "video_id": vid,
                    "channel_id": cid,
                    "strategy_name": s.get("strategy_name", "Unknown"),
                    "strategy_type": s.get("strategy_type"),
                    "timeframe": s.get("timeframe"),
                    "indicators": json.dumps(s.get("indicators", [])),
                    "entry_rules": s.get("entry_rules"),
                    "exit_rules": s.get("exit_rules"),
                    "stop_loss_rules": s.get("stop_loss_rules"),
                    "risk_management": s.get("risk_management"),
                    "backtested_results": s.get("backtested_results"),
                    "key_quotes": s.get("key_quotes"),
                    "confidence_score": s.get("confidence_score"),
                    "markets": s.get("markets"),
                    "summary": s.get("summary"),
                }
                supabase_post("yt_strategies", record)
            strategies_found += len(all_strategies)
            print(f"OK ({len(all_strategies)} strategies)", file=sys.stderr)
        else:
            print("no strategies", file=sys.stderr)

        supabase_patch("yt_videos", "video_id", vid, {
            "analysis_status": "analyzed",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        analyzed += 1
        time.sleep(1)

    result = {
        "analyzed": analyzed,
        "strategies_found": strategies_found,
        "skipped": skipped,
    }
    print(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description="Analyze transcripts for trading strategies")
    parser.add_argument("--batch", type=int, default=10, help="Batch size (default 10)")
    parser.add_argument("--channel", type=str, help="Filter by channel_id")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_KEY required", file=sys.stderr)
        sys.exit(1)

    analyze_videos(batch_size=args.batch, channel_id=args.channel)


if __name__ == "__main__":
    main()
