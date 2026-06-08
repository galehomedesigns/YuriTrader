#!/usr/bin/env python3
"""Build canvas/agent-overview.html from the locked template + data lists below.

Data lists below are the SINGLE SOURCE OF TRUTH for the dashboard's job/model
assignments. When you add a cron entry to crontab.gx10, add a corresponding row
here. When you swap which model a job uses, edit the row here.

Per ~/openclaw/docs/DASHBOARDS.md: the template never changes between runs,
only the injected JSON blob does. Do not hand-edit canvas/agent-overview.html.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

OPENCLAW = Path("/home/tonygale/openclaw")
TEMPLATE = Path(__file__).resolve().parent / "agent-overview.template.html"
OUT = OPENCLAW / "canvas/agent-overview.html"


# ---------------------------------------------------------------------------
# LLM-driven cron jobs — every row corresponds 1:1 to a line in crontab.gx10
# that actually invokes a local Ollama model. 14 rows total.
# ---------------------------------------------------------------------------
LLM_JOBS = [
    {
        "job": "trading-news-scan",
        "schedule": "*/15 min, 24/7",
        "model": {"name": "quick36", "note": "(impact synth, on alerts)"},
        "delivery": "Telegram (HIGH only)",
        "status": "OK",
    },
    {
        "job": "trading-premarket-briefing",
        "schedule": "9:00 AM Mon-Fri",
        "model": {"name": "quick36", "note": "(narrative synth)"},
        "delivery": "Telegram",
        "status": "OK",
    },
    {
        "job": "trading-postmarket-summary",
        "schedule": "4:30 PM Mon-Fri",
        "model": {"name": "quick36", "note": "(narrative synth)"},
        "delivery": "Telegram",
        "status": "OK",
    },
    {
        "job": "trading-dashboard",
        "schedule": "12:00 PM Mon-Fri",
        "model": {"name": "quick36", "note": "(market commentary card)"},
        "delivery": "Silent (HTML)",
        "status": "OK",
    },
    {
        "job": "watchlist",
        "schedule": "9 AM, 11 AM, 1 PM, 3 PM Mon-Fri",
        "model": {"name": "quick36", "note": "(tradability narrative)"},
        "delivery": "Telegram",
        "status": "OK",
    },
    {
        "job": "buy-watcher (crypto)",
        "schedule": "*/30 min, 24/7",
        "model": {"name": "quick", "note": "(→ quick36, on signal, via advisor)"},
        "delivery": "Telegram (on signal)",
        "status": "OK",
    },
    {
        "job": "stock-buy-watcher",
        "schedule": "*/30 min, 24/7",
        "model": {"name": "quick", "note": "(→ quick36, on signal, via advisor)"},
        "delivery": "Telegram (on signal)",
        "status": "OK",
    },
    {
        "job": "overseer-game_plan",
        "schedule": "9:00 AM Mon-Fri",
        "model": {"name": "quick36", "note": "(strategic narrative)"},
        "delivery": "Telegram",
        "status": "OK",
    },
    {
        "job": "overseer-autopsy",
        "schedule": "4:30 PM Mon-Fri",
        "model": {"name": "quick36", "note": "+ quality fallback"},
        "delivery": "Telegram",
        "status": "OK",
    },
    {
        "job": "overseer-super_prompt",
        "schedule": "6:00 PM Fri",
        "model": {"name": "quick36", "note": "+ quality fallback"},
        "delivery": "Telegram",
        "status": "OK",
    },
    {
        "job": "medic-morning",
        "schedule": "7:00 AM Mon-Fri",
        "model": {"name": "quick36", "note": "(narrative synth)"},
        "delivery": "Telegram",
        "status": "OK",
    },
    {
        "job": "medic-evening",
        "schedule": "11:00 PM Daily",
        "model": {"name": "quick36", "note": "(narrative synth)"},
        "delivery": "Telegram",
        "status": "OK",
    },
    {
        "job": "receipts-cron",
        "schedule": "11:00 PM Daily",
        "model": {"name": "qwen2.5vl:72b", "note": "(local vision)"},
        "delivery": "Telegram (on processed)",
        "status": "OK",
    },
    {
        "job": "procurement-crawl",
        "schedule": "3:00 AM, every 2 days",
        "model": {"name": "quality", "note": "(tender fit digest)"},
        "delivery": "Telegram",
        "status": "OK",
    },
]


# ---------------------------------------------------------------------------
# Background daemons — algorithmic / data plumbing, no LLM by design. 6 rows.
# ---------------------------------------------------------------------------
BACKGROUND_DAEMONS = [
    {
        "job": "arena-scan (intraday)",
        "schedule": "*/5 min 9 AM–3 PM Mon-Fri",
        "what_it_does": "10-bot TAY scan against price ticks; algorithmic entries",
        "delivery": "Silent (paper trades to DB)",
        "status": "OK",
    },
    {
        "job": "arena-scan (open)",
        "schedule": "9:30 AM Mon-Fri",
        "what_it_does": "Market-open arena run",
        "delivery": "Silent",
        "status": "OK",
    },
    {
        "job": "tv-focus",
        "schedule": "*/30 min 9 AM–3 PM Mon-Fri",
        "what_it_does": "Rotates TradingView focus pair via API",
        "delivery": "Silent",
        "status": "OK",
    },
    {
        "job": "position-watcher",
        "schedule": "*/5 min, 24/7",
        "what_it_does": "Checks TP/SL on open crypto positions, fires algorithmic exits",
        "delivery": "Telegram (on TP/SL hit)",
        "status": "OK",
    },
    {
        "job": "stock-position-watcher",
        "schedule": "*/5 min, 24/7",
        "what_it_does": "Same for stock positions",
        "delivery": "Telegram (on TP/SL hit)",
        "status": "OK",
    },
    {
        "job": "questrade-token-refresh",
        "schedule": "*/20 min, 24/7",
        "what_it_does": "OAuth token refresh (auth plumbing only)",
        "delivery": "Silent",
        "status": "OK",
    },
]


# ---------------------------------------------------------------------------
# Local models actually installed on GX10. Pulled from `ollama list` and the
# advisor/cron scripts that consume them.
# ---------------------------------------------------------------------------
LOCAL_MODELS = [
    {"name": "quick:latest",       "size": "23 GB",  "role": "Trading advisor (qwen3.5:35b)"},
    {"name": "quick36:latest",     "size": "23 GB",  "role": "Narrative synth for medic / game_plan / pre+post-market / overseer-autopsy / super_prompt (qwen3.6 MoE, ~3B active)"},
    {"name": "quality:latest",     "size": "38 GB",  "role": "Heavy reasoning / overseer fallback / yt-strategy / email-receipt extraction (qwen3.6 35B-A3B Q8)"},
    {"name": "qwen2.5vl:72b",      "size": "48 GB",  "role": "Vision OCR for receipts (replaces Gemini Vision)"},
    {"name": "coder:latest",       "size": "51 GB",  "role": "Code generation (qwen3-coder-next), available but not cron-wired"},
    {"name": "mxbai-embed-large",  "size": "669 MB", "role": "Embeddings (memory-sync, procurement, medic event log)"},
    {"name": "gemma4:31b",         "size": "19 GB",  "role": "installed but not actively used", "dim": True},
]


def main() -> int:
    if not TEMPLATE.exists():
        print(f"Template missing: {TEMPLATE}", flush=True)
        return 1

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "llm_jobs": LLM_JOBS,
        "bg_daemons": BACKGROUND_DAEMONS,
        "local_models": LOCAL_MODELS,
    }

    template = TEMPLATE.read_text()
    html = template.replace("{{DATA}}", json.dumps(data, default=str))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html)
    print(f"Wrote {OUT} ({len(html)} bytes, "
          f"{len(LLM_JOBS)} LLM jobs, "
          f"{len(BACKGROUND_DAEMONS)} background daemons, "
          f"{len(LOCAL_MODELS)} local models)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
