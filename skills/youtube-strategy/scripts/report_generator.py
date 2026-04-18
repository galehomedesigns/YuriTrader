#!/usr/bin/env python3
"""Generate a consolidated strategy report for a YouTube channel."""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}
CANVAS_DIR = "/data/.openclaw/canvas"


def supabase_get(table, params):
    url = f"{SUPABASE_URL}/rest/v1/{table}?{params}"
    r = httpx.get(url, headers=HEADERS, timeout=30)
    return r.json() if r.status_code < 400 else []


def get_channel_info(channel_id):
    rows = supabase_get("yt_channels", f"channel_id=eq.{channel_id}&limit=1")
    return rows[0] if rows else {}


def get_video_stats(channel_id):
    total = supabase_get("yt_videos", f"channel_id=eq.{channel_id}&select=video_id&limit=0&head=true")
    transcribed = supabase_get("yt_videos",
        f"channel_id=eq.{channel_id}&transcript_status=eq.transcribed&select=count")
    analyzed = supabase_get("yt_videos",
        f"channel_id=eq.{channel_id}&analysis_status=eq.analyzed&select=count")
    return {
        "transcribed": transcribed[0]["count"] if transcribed else 0,
        "analyzed": analyzed[0]["count"] if analyzed else 0,
    }


def get_strategies(channel_id):
    return supabase_get("yt_strategies",
        f"channel_id=eq.{channel_id}&order=confidence_score.desc.nullslast&limit=500")


def generate_markdown(channel_id):
    channel = get_channel_info(channel_id)
    strategies = get_strategies(channel_id)

    if not strategies:
        return f"# No strategies found for channel {channel_id}\n\nRun the analyzer first."

    # Group by strategy type
    by_type = defaultdict(list)
    for s in strategies:
        stype = s.get("strategy_type") or "other"
        by_type[stype].append(s)

    # Deduplicate by name similarity (simple approach: group by strategy_name)
    unique_strategies = {}
    for s in strategies:
        name = s.get("strategy_name", "").lower().strip()
        if name not in unique_strategies or (s.get("confidence_score") or 0) > (unique_strategies[name].get("confidence_score") or 0):
            unique_strategies[name] = s

    ranked = sorted(unique_strategies.values(), key=lambda x: -(x.get("confidence_score") or 0))

    # Build report
    lines = []
    channel_name = channel.get("channel_name", channel_id)
    lines.append(f"# Trading Strategy Report: {channel_name}")
    lines.append(f"*Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n")

    lines.append(f"## Overview")
    lines.append(f"- **Channel:** {channel_name}")
    lines.append(f"- **Total strategies extracted:** {len(strategies)}")
    lines.append(f"- **Unique strategies:** {len(unique_strategies)}")
    lines.append(f"- **Strategy types:** {', '.join(sorted(by_type.keys()))}")
    lines.append("")

    # Top strategies
    lines.append("## Top Strategies (Ranked by Confidence)\n")
    for i, s in enumerate(ranked[:20], 1):
        score = s.get("confidence_score") or 0
        stars = "+" * score
        lines.append(f"### {i}. {s.get('strategy_name', 'Unknown')} [{stars}]")
        lines.append(f"**Type:** {s.get('strategy_type', 'N/A')} | "
                     f"**Timeframe:** {s.get('timeframe', 'N/A')} | "
                     f"**Markets:** {s.get('markets', 'N/A')}")
        lines.append("")

        if s.get("summary"):
            lines.append(f"**Summary:** {s['summary']}")
            lines.append("")

        indicators = s.get("indicators")
        if indicators:
            if isinstance(indicators, str):
                try:
                    indicators = json.loads(indicators)
                except (json.JSONDecodeError, TypeError):
                    indicators = [indicators]
            if indicators:
                lines.append(f"**Indicators:** {', '.join(str(ind) for ind in indicators)}")
                lines.append("")

        if s.get("entry_rules"):
            lines.append(f"**Entry Rules:**")
            lines.append(f"{s['entry_rules']}")
            lines.append("")
        if s.get("exit_rules"):
            lines.append(f"**Exit Rules:**")
            lines.append(f"{s['exit_rules']}")
            lines.append("")
        if s.get("stop_loss_rules"):
            lines.append(f"**Stop Loss:**")
            lines.append(f"{s['stop_loss_rules']}")
            lines.append("")
        if s.get("risk_management"):
            lines.append(f"**Risk Management:**")
            lines.append(f"{s['risk_management']}")
            lines.append("")
        if s.get("backtested_results"):
            lines.append(f"**Backtested Results:**")
            lines.append(f"{s['backtested_results']}")
            lines.append("")
        if s.get("key_quotes"):
            lines.append(f"> {s['key_quotes']}")
            lines.append("")

        lines.append("---\n")

    # Strategy type breakdown
    lines.append("## Strategy Type Breakdown\n")
    lines.append("| Type | Count | Avg Confidence |")
    lines.append("|------|-------|----------------|")
    for stype, items in sorted(by_type.items()):
        avg_conf = sum((s.get("confidence_score") or 0) for s in items) / len(items)
        lines.append(f"| {stype} | {len(items)} | {avg_conf:.1f}/5 |")
    lines.append("")

    # Common indicators
    indicator_counts = defaultdict(int)
    for s in strategies:
        indicators = s.get("indicators")
        if isinstance(indicators, str):
            try:
                indicators = json.loads(indicators)
            except (json.JSONDecodeError, TypeError):
                continue
        if isinstance(indicators, list):
            for ind in indicators:
                indicator_counts[str(ind).strip()] += 1

    if indicator_counts:
        lines.append("## Most Referenced Indicators\n")
        lines.append("| Indicator | Mentions |")
        lines.append("|-----------|----------|")
        for ind, count in sorted(indicator_counts.items(), key=lambda x: -x[1])[:15]:
            lines.append(f"| {ind} | {count} |")
        lines.append("")

    return "\n".join(lines)


def generate_html(markdown_content, channel_name):
    """Generate a dark-themed HTML dashboard from the report."""
    # Simple markdown to HTML conversion for the dashboard
    import re
    html_body = markdown_content
    # Headers
    html_body = re.sub(r"^### (.+)$", r"<h3>\1</h3>", html_body, flags=re.MULTILINE)
    html_body = re.sub(r"^## (.+)$", r"<h2>\1</h2>", html_body, flags=re.MULTILINE)
    html_body = re.sub(r"^# (.+)$", r"<h1>\1</h1>", html_body, flags=re.MULTILINE)
    # Bold
    html_body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html_body)
    # Italic
    html_body = re.sub(r"\*(.+?)\*", r"<em>\1</em>", html_body)
    # Blockquotes
    html_body = re.sub(r"^> (.+)$", r"<blockquote>\1</blockquote>", html_body, flags=re.MULTILINE)
    # HR
    html_body = re.sub(r"^---$", r"<hr>", html_body, flags=re.MULTILINE)
    # List items
    html_body = re.sub(r"^- (.+)$", r"<li>\1</li>", html_body, flags=re.MULTILINE)
    # Tables
    lines = html_body.split("\n")
    in_table = False
    new_lines = []
    for line in lines:
        if line.startswith("|") and line.endswith("|"):
            if "---" in line:
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not in_table:
                new_lines.append("<table>")
                new_lines.append("<tr>" + "".join(f"<th>{c}</th>" for c in cells) + "</tr>")
                in_table = True
            else:
                new_lines.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
        else:
            if in_table:
                new_lines.append("</table>")
                in_table = False
            new_lines.append(line)
    if in_table:
        new_lines.append("</table>")
    html_body = "\n".join(new_lines)
    # Paragraphs
    html_body = re.sub(r"\n\n+", "\n<br>\n", html_body)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Strategy Report: {channel_name}</title>
<style>
  body {{ background: #0a0e1a; color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; line-height: 1.6; }}
  h1 {{ color: #7c8aff; border-bottom: 2px solid #7c8aff; padding-bottom: 10px; }}
  h2 {{ color: #9fa8da; margin-top: 30px; }}
  h3 {{ color: #b0bec5; }}
  strong {{ color: #fff; }}
  blockquote {{ border-left: 3px solid #7c8aff; padding-left: 15px; color: #aaa; font-style: italic; }}
  hr {{ border: none; border-top: 1px solid #333; margin: 20px 0; }}
  table {{ width: 100%; border-collapse: collapse; margin: 15px 0; }}
  th {{ background: #1a1f3a; color: #7c8aff; padding: 10px; text-align: left; }}
  td {{ padding: 8px 10px; border-bottom: 1px solid #222; }}
  tr:hover td {{ background: #111528; }}
  li {{ margin: 4px 0; }}
  em {{ color: #888; }}
</style>
</head>
<body>
{html_body}
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Generate strategy report")
    parser.add_argument("channel_id", help="Channel ID to report on")
    parser.add_argument("--format", choices=["markdown", "html", "both"], default="both")
    parser.add_argument("--output", type=str, help="Output file path")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_KEY required", file=sys.stderr)
        sys.exit(1)

    channel = get_channel_info(args.channel_id)
    channel_name = channel.get("channel_name", args.channel_id)

    md = generate_markdown(args.channel_id)

    if args.format in ("markdown", "both"):
        md_path = args.output or f"/data/.openclaw/workspace/projects/youtube-strategy/{channel_name.replace(' ', '-').lower()}-strategies.md"
        with open(md_path, "w") as f:
            f.write(md)
        print(f"Markdown report: {md_path}", file=sys.stderr)

    if args.format in ("html", "both"):
        html = generate_html(md, channel_name)
        html_path = f"{CANVAS_DIR}/yt-strategies-{channel_name.replace(' ', '-').lower()}.html"
        with open(html_path, "w") as f:
            f.write(html)
        # Fix ownership for Caddy
        os.system(f"chown ubuntu:ubuntu '{html_path}' 2>/dev/null")
        print(f"HTML dashboard: {html_path}", file=sys.stderr)

    if args.format == "markdown":
        print(md)
    else:
        print(f"Reports generated for {channel_name}")
        print(f"  Strategies: {len(get_strategies(args.channel_id))}")


if __name__ == "__main__":
    main()
