#!/usr/bin/env python3
"""Daily Firing Report — crypto-bot activity over the last 24h.

Posts a Telegram summary to the same chat as buy_watcher: how many fires
per bot, paper vs live split, and any "Live blocked: ..." reasons grepped
from the arena scan logs. Designed for one daily cron firing — see
firing_report_cron.sh.
"""
import html
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone


def _load_env():
    env_file = "/home/tonygale/openclaw/.env"
    if not os.path.exists(env_file):
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k and v:
                os.environ.setdefault(k, v)


_load_env()

SB_URL = os.environ["SUPABASE_URL"]
SB_KEY = os.environ["SUPABASE_SERVICE_KEY"]
TG_TOKEN = os.environ.get("TELEGRAM_TRADER_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "6545739863")

# Cover exactly the bots cleared for live money (env-driven so the report
# can't drift behind LIVE_TRADING_BOTS). _load_env() above has already
# populated os.environ from .env, so this works under cron and standalone.
CRYPTO_BOTS = [
    b.strip()
    for b in os.environ.get(
        "LIVE_TRADING_BOTS",
        "trap-catcher,momentum-hunter,correlation-hunter,squeeze-breaker",
    ).split(",")
    if b.strip()
]
ARENA_LOGS = [
    "/home/tonygale/openclaw/skills/trading-arena/logs/arena_scan.log",
    "/home/tonygale/openclaw/skills/trading-arena/logs/arena_scan_crypto.log",
]


def sb_get(path):
    req = urllib.request.Request(
        f"{SB_URL}/rest/v1/{path}",
        headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def tg_send(text):
    if not TG_TOKEN:
        print("TELEGRAM_TRADER_BOT_TOKEN not set — printing instead", file=sys.stderr)
        print(text)
        return
    data = urllib.parse.urlencode({
        "chat_id": TG_CHAT,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=data, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def count_fires(rows, since_iso):
    counts = defaultdict(int)
    for r in rows:
        if r.get("opened_at", "") < since_iso:
            continue
        bot = r["bot_id"]
        mode = "live" if r.get("paper") is False else "paper"
        counts[(bot, mode)] += 1
    return counts


def tail_blocked_reasons(hours=24):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
    reasons = Counter()
    for log in ARENA_LOGS:
        if not os.path.exists(log):
            continue
        try:
            out = subprocess.check_output(["tail", "-n", "20000", log], text=True)
        except Exception:
            continue
        for line in out.splitlines():
            m = re.search(r"Live blocked: ([^—]+?)(?:\s+—|$)", line)
            if m:
                reasons[m.group(1).strip()] += 1
    return reasons


def main():
    now = datetime.now(timezone.utc)
    # Z-suffix UTC, URL-encoded (the ":" in time would otherwise confuse PostgREST).
    since_24h = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    since_48h = (now - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    since_48h_q = urllib.parse.quote(since_48h, safe="")

    rows = []
    for b in CRYPTO_BOTS:
        rows.extend(sb_get(
            f"arena_trades?bot_id=eq.{b}&opened_at=gte.{since_48h_q}"
            "&select=bot_id,opened_at,paper,symbol&order=opened_at.asc&limit=2000"
        ))

    last_24 = count_fires(rows, since_24h)
    prior_24 = {k: v for k, v in count_fires(rows, since_48h).items()}
    # prior_24 currently spans the full 48h. Subtract last_24 to get the prior window.
    prior_24 = {k: prior_24.get(k, 0) - last_24.get(k, 0) for k in set(prior_24) | set(last_24)}

    def row(bot):
        lp = last_24[(bot, "paper")]
        ll = last_24[(bot, "live")]
        pp = prior_24.get((bot, "paper"), 0)
        pl = prior_24.get((bot, "live"), 0)
        return (bot, lp, ll, pp, pl)

    reasons = tail_blocked_reasons(hours=24)

    lines = [
        f"<b>📈 Crypto-Bot Daily Firing</b>  ({now.strftime('%Y-%m-%d %H:%M UTC')})",
        "",
        "<b>Fires per bot (last 24h vs prior 24h)</b>",
        "<pre>",
        f"{'bot':<18} {'paper':>6} {'live':>6}   {'prior P':>8} {'prior L':>8}",
    ]
    for b in CRYPTO_BOTS:
        bot, lp, ll, pp, pl = row(b)
        lines.append(f"{bot:<18} {lp:>6} {ll:>6}   {pp:>8} {pl:>8}")
    lines.append("</pre>")

    if reasons:
        lines.append("")
        lines.append("<b>Live blocked (top reasons, 24h)</b>")
        lines.append("<pre>")
        for reason, n in reasons.most_common(5):
            r = html.escape(reason)[:60]
            lines.append(f"{n:>4}x  {r}")
        lines.append("</pre>")
    else:
        lines.append("")
        lines.append("<i>No 'Live blocked' lines in scan logs (last 24h).</i>")

    tg_send("\n".join(lines))


if __name__ == "__main__":
    main()
