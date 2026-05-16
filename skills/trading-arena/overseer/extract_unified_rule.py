#!/usr/bin/env python3
"""One-shot YouTube Strategy Digester.

Reads all 98 strategies from yt_strategies, calls GX10 'quick' model to
synthesize them into a unified TAY framework digest, saves to STRATEGY_DIGEST.md.

This is a one-time analysis to inform bot variable tuning. Not a runtime
dependency.

Usage:
    python3 extract_unified_rule.py
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SUPABASE_URL, SUPABASE_KEY

OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
MODEL = "quick36"
OUTPUT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "STRATEGY_DIGEST.md"
)

PROMPT_TEMPLATE = """You are a trading strategy analyst. I have extracted {n} trading strategies from YouTube videos. They all follow some variation of the TAY framework: Trend (T), Area of value (A), Entry trigger (Y).

Your job: synthesize these strategies into a unified digest organized by the TAY framework. For each component (T, A, Y), list the most common definitions and their frequency.

STRATEGIES:
{strategies}

Output format (markdown):

# TAY Framework Synthesis from {n} YouTube Strategies

## Most Common TREND Definitions
- [definition] (mentioned by N strategies)
- ...

## Most Common AREA OF VALUE Definitions
- [definition] (mentioned by N strategies)
- ...

## Most Common ENTRY TRIGGER Definitions
- [definition] (mentioned by N strategies)
- ...

## Top 5 Most Frequently Mentioned Indicators
- [indicator] (used by N strategies)
- ...

## Recommended Default TAY Setup (synthesized from the 98)
**T:** [recommended trend definition]
**A:** [recommended value definition]
**Y:** [recommended trigger definition]

## Key Insights for Bot Refinement
[3-5 short bullet points on what the data tells us about which patterns work]

Be concise. No filler. Use the data to back every claim with a count.
"""


def supabase_get(path):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def call_ollama(prompt):
    url = f"{OLLAMA_URL}/api/generate"
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"num_ctx": 32768, "temperature": 0.3},
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read().decode()).get("response", "")


def main():
    print("Loading strategies from Supabase...", file=sys.stderr)
    strategies = supabase_get(
        "yt_strategies?select=strategy_name,strategy_type,timeframe,indicators,entry_rules,exit_rules,stop_loss_rules,confidence_score&confidence_score=gte.3&order=confidence_score.desc&limit=100"
    )
    print(f"Loaded {len(strategies)} high-confidence strategies", file=sys.stderr)

    if not strategies:
        print("No strategies found")
        sys.exit(1)

    # Compact format to fit context
    compact = []
    for i, s in enumerate(strategies, 1):
        ind = s.get("indicators")
        if isinstance(ind, list):
            ind_str = ", ".join(ind[:5])
        else:
            ind_str = str(ind or "")[:100]
        entry = (s.get("entry_rules") or "")[:300]
        compact.append(
            f"{i}. [{s.get('strategy_type','')}] {s.get('strategy_name','')[:70]}\n"
            f"   Indicators: {ind_str}\n"
            f"   Entry: {entry}"
        )
    strategies_text = "\n".join(compact)

    prompt = PROMPT_TEMPLATE.format(n=len(strategies), strategies=strategies_text)
    print(f"Prompt size: {len(prompt)} chars", file=sys.stderr)
    print(f"Calling GX10 {MODEL}...", file=sys.stderr)

    response = call_ollama(prompt)
    if not response:
        print("Empty response from Ollama", file=sys.stderr)
        sys.exit(1)

    header = (
        f"<!-- Generated {datetime.now(timezone.utc).isoformat()} from {len(strategies)} strategies -->\n"
        f"<!-- Source: Supabase yt_strategies table | Model: {MODEL} -->\n\n"
    )
    with open(OUTPUT_FILE, "w") as f:
        f.write(header + response)

    print(f"\nSaved to {OUTPUT_FILE}")
    print(f"\n=== First 30 lines ===")
    print("\n".join(response.splitlines()[:30]))


if __name__ == "__main__":
    main()
