#!/usr/bin/env python3
"""
Trading dashboard generator — creates trading.html in the canvas directory.

Usage:
    python3 dashboard_gen.py generate    # Regenerate trading.html
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
# Path-aware: /data/.openclaw is the legacy VPS path; on GX10 use the home path.
CANVAS_DIR = (Path("/data/.openclaw/canvas") if Path("/data/.openclaw").exists()
              else Path("/home/tonygale/openclaw/canvas"))
OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


def supabase_get(table, params=None):
    resp = httpx.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=HEADERS,
        params=params or {},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def generate_market_commentary(latest, latest_signals, alerts, news, auto_open,
                               auto_today, auto_daily_pnl):
    """Use quick36 to synthesize 3-5 sentence market commentary for the dashboard.
    Returns plain text or None on failure."""
    movers = sorted(latest.values(), key=lambda s: abs(float(s.get("day_change_pct") or 0)), reverse=True)[:8]
    mover_lines = []
    for s in movers:
        pct = float(s.get("day_change_pct") or 0)
        mover_lines.append(f"  {s['symbol']:<8} {pct:+6.2f}%  vol={int(s.get('volume') or 0):,}")

    sig_changes = [s for s in latest_signals.values() if s.get("signal_changed")]
    sig_lines = [f"  {s['symbol']}: {s.get('previous_signal','?')} -> {s['signal']}" for s in sig_changes[:6]]

    triggered = [a for a in alerts if a.get("triggered")]
    alert_lines = [f"  {a.get('symbol','?')}: {a.get('alert_type','?')} threshold {a.get('threshold','?')}" for a in triggered[:6]]

    high_news = [n for n in news if n.get("impact_level") == "HIGH"][:5]
    news_lines = [f"  [{n.get('source','?')}] {n.get('title','')[:120]}" for n in high_news]

    prompt = f"""You are writing the daily market commentary block for Tony's trading dashboard.
Produce a tight market read (3-5 sentences, plain text, no markdown).

Lead with the dominant theme today (broad direction, sector rotation, risk-on/off).
Cite specific tickers with their moves. Note signal flips and high-impact news if relevant.
Skip the auto-trader unless P&L is notable. Do not invent numbers.

Top movers today:
{chr(10).join(mover_lines) if mover_lines else '  (no data)'}

Signal changes:
{chr(10).join(sig_lines) if sig_lines else '  (none)'}

Triggered alerts:
{chr(10).join(alert_lines) if alert_lines else '  (none)'}

High-impact news:
{chr(10).join(news_lines) if news_lines else '  (none)'}

Auto-trader: {len(auto_open)} open positions, ${auto_daily_pnl:+.2f} P&L today ({len(auto_today)} trades)."""

    payload = json.dumps({
        "model": "quick36:latest",
        "prompt": prompt,
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {"temperature": 0.4, "num_ctx": 8192, "num_predict": 500},
    }).encode()

    import urllib.request
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate", data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read()).get("response", "").strip() or None
    except Exception as e:
        print(f"  commentary unavailable: {type(e).__name__}: {str(e)[:100]}", file=sys.stderr)
        return None


def cmd_generate():
    now = datetime.utcnow()
    updated = now.strftime("%B %d, %Y at %I:%M %p UTC")

    # Fetch data
    snapshots = supabase_get("market_snapshots", {
        "select": "symbol,price,day_change_pct,volume,bid,ask,snapshot_at",
        "order": "snapshot_at.desc",
        "limit": "50",
    })

    # Deduplicate to latest per symbol
    latest = {}
    for s in snapshots:
        if s["symbol"] not in latest:
            latest[s["symbol"]] = s

    alerts = supabase_get("price_alerts", {
        "enabled": "eq.true",
        "select": "*",
        "order": "created_at.desc",
    })

    signals = supabase_get("trend_signals", {
        "select": "symbol,signal,previous_signal,signal_changed,sma_5,sma_20,volume_ratio,computed_at",
        "order": "computed_at.desc",
        "limit": "30",
    })

    # Deduplicate signals to latest per symbol
    latest_signals = {}
    for s in signals:
        if s["symbol"] not in latest_signals:
            latest_signals[s["symbol"]] = s

    news = supabase_get("news_events", {
        "select": "title,source,impact_level,published_at,url",
        "order": "fetched_at.desc",
        "limit": "15",
    })

    social = supabase_get("social_signals", {
        "market_relevant": "eq.true",
        "select": "platform,author,content,severity,fetched_at",
        "order": "fetched_at.desc",
        "limit": "15",
    })

    watchlist = []
    cfg = supabase_get("trading_config", {"key": "eq.watchlist", "select": "value"})
    if cfg:
        watchlist = cfg[0]["value"] if isinstance(cfg[0]["value"], list) else json.loads(cfg[0]["value"])

    # Auto-trade data
    auto_open = supabase_get("auto_trades", {
        "status": "eq.OPEN", "select": "*", "order": "opened_at.desc",
    })
    auto_closed = supabase_get("auto_trades", {
        "status": "eq.CLOSED", "select": "*",
        "order": "closed_at.desc", "limit": "15",
    })
    today_start = now.replace(hour=0, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    auto_today = [t for t in auto_closed if t.get("closed_at", "") >= today_start]
    auto_daily_pnl = sum(float(t["pnl"]) for t in auto_today if t.get("pnl"))

    # Risk status
    risk_cfg = supabase_get("trading_rules", {"key": "eq.risk_limits", "select": "value"})
    risk = risk_cfg[0]["value"] if risk_cfg else {}
    auto_paused = risk.get("auto_trading_paused", False)

    # Build HTML
    positions_rows = ""
    for sym, s in sorted(latest.items()):
        price = float(s["price"])
        pct = float(s["day_change_pct"]) if s.get("day_change_pct") else 0
        vol = int(s["volume"]) if s.get("volume") else 0
        sig_data = latest_signals.get(sym)
        signal_badge = ""
        if sig_data:
            sig = sig_data["signal"]
            color = "#00c853" if sig == "BULLISH" else "#ff1744" if sig == "BEARISH" else "#ffc107"
            signal_badge = f'<span style="color:{color};font-weight:600">{sig}</span>'

        pct_color = "#00c853" if pct > 0 else "#ff1744" if pct < 0 else "#8899aa"
        positions_rows += f"""<tr>
            <td style="font-weight:600">{sym}</td>
            <td>${price:,.2f}</td>
            <td style="color:{pct_color}">{pct:+.2f}%</td>
            <td>{vol:,}</td>
            <td>{signal_badge}</td>
        </tr>"""

    alerts_rows = ""
    for a in alerts:
        status = '<span style="color:#ff1744">TRIGGERED</span>' if a["triggered"] else '<span style="color:#00c853">Active</span>'
        alerts_rows += f"""<tr>
            <td>{a['symbol']}</td>
            <td>{a['alert_type']}</td>
            <td>${float(a['threshold']):,.2f}</td>
            <td>{status}</td>
        </tr>"""

    news_items = ""
    for n in news:
        impact_color = "#ff1744" if n.get("impact_level") == "HIGH" else "#ffc107" if n.get("impact_level") == "MEDIUM" else "#8899aa"
        badge = f'<span style="background:{impact_color}22;color:{impact_color};padding:2px 8px;border-radius:4px;font-size:11px">{n.get("impact_level", "—")}</span>'
        url = n.get("url", "#")
        title = n["title"][:100]
        source = n.get("source", "")
        news_items += f'<div style="padding:8px 0;border-bottom:1px solid #1a2332"><a href="{url}" target="_blank" style="color:#7c8aff;text-decoration:none">{title}</a> <span style="color:#556677;font-size:12px">— {source}</span> {badge}</div>'

    social_items = ""
    for s in social:
        sev = s.get("severity", "LOW")
        sev_color = "#ff1744" if sev == "HIGH" else "#ffc107" if sev == "MEDIUM" else "#8899aa"
        badge = f'<span style="background:{sev_color}22;color:{sev_color};padding:2px 8px;border-radius:4px;font-size:11px">{sev}</span>'
        content = s["content"][:150]
        platform = s.get("platform", "")
        author = s.get("author", "")
        social_items += f'<div style="padding:8px 0;border-bottom:1px solid #1a2332"><strong style="color:#ccd6dd">{author}</strong> <span style="color:#556677;font-size:12px">({platform})</span> {badge}<br><span style="color:#8899aa;font-size:13px">{content}</span></div>'

    signal_changes = ""
    for sym, s in latest_signals.items():
        if s.get("signal_changed"):
            signal_changes += f'<div style="padding:6px 0;color:#ffc107">{sym}: {s.get("previous_signal","?")} → {s["signal"]}</div>'

    # Auto-trade rows
    auto_positions_rows = ""
    for p in auto_open:
        entry = float(p["entry_price"])
        # Get current price from snapshots if available
        sym_snap = latest.get(p["symbol"])
        cur = float(sym_snap["price"]) if sym_snap else entry
        if p["side"] == "BUY":
            ret = ((cur - entry) / entry) * 100
        else:
            ret = ((entry - cur) / entry) * 100
        ret_color = "#00c853" if ret > 0 else "#ff1744"
        flags = p.get("buy_flags_met", [])
        flags_str = ", ".join(flags[:3]) + ("..." if len(flags) > 3 else "")
        opened = p["opened_at"][:10]
        auto_positions_rows += f'''<tr>
            <td style="font-weight:600">{p["symbol"]}</td>
            <td>{p["side"]}</td>
            <td>{float(p["qty"]):.4f}</td>
            <td>${entry:.2f}</td>
            <td>${cur:.2f}</td>
            <td style="color:{ret_color}">{ret:+.1f}%</td>
            <td style="font-size:12px;color:#8899aa">{flags_str}</td>
        </tr>'''

    auto_closed_rows = ""
    for t in auto_closed[:10]:
        pnl = float(t["pnl"]) if t.get("pnl") else 0
        pnl_color = "#00c853" if pnl >= 0 else "#ff1744"
        sign = "+" if pnl >= 0 else ""
        dt = (t.get("closed_at") or "")[:10]
        auto_closed_rows += f'''<tr>
            <td>{t["symbol"]}</td>
            <td>{t["side"]}</td>
            <td>${float(t["entry_price"]):.2f}</td>
            <td>${float(t.get("exit_price",0)):.2f}</td>
            <td style="color:{pnl_color}">{sign}${pnl:.2f}</td>
            <td>{t.get("sell_reason","")}</td>
            <td>{dt}</td>
        </tr>'''

    auto_pnl_color = "#00c853" if auto_daily_pnl >= 0 else "#ff1744"
    auto_status_badge = '<span style="color:#ff1744">PAUSED</span>' if auto_paused else '<span style="color:#00c853">ACTIVE</span>'

    # LLM-generated market commentary (quick36)
    import html as _html_mod
    commentary = generate_market_commentary(
        latest, latest_signals, alerts, news, auto_open, auto_today, auto_daily_pnl
    )
    commentary_card = ""
    if commentary:
        safe = _html_mod.escape(commentary).replace("\n", "<br>")
        commentary_card = f'''
        <div class="card full-width" style="background:linear-gradient(135deg,#161b22 0%,#1a2332 100%);border-left:3px solid #7c8aff;">
            <h2>🤖 Market Read <span style="font-weight:normal;font-size:12px;color:#556677;">— quick36 (qwen3.6 MoE)</span></h2>
            <div style="color:#ccd6dd;font-size:14px;line-height:1.6;">{safe}</div>
        </div>'''

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Trading Intelligence — Yuri</title>
    <style>
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ background:#0d1117; color:#ccd6dd; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; padding:20px; }}
        .header {{ text-align:center; padding:30px 0 20px; border-bottom:1px solid #1a2332; margin-bottom:30px; }}
        .header h1 {{ font-size:28px; color:#ffffff; font-weight:700; }}
        .header .subtitle {{ color:#8899aa; font-size:14px; margin-top:6px; }}
        .header .updated {{ color:#556677; font-size:12px; margin-top:4px; }}
        .stat-row {{ display:flex; gap:16px; margin-bottom:24px; max-width:1200px; margin:0 auto 24px; }}
        .stat {{ flex:1; background:#161b22; border:1px solid #21262d; border-radius:12px; padding:20px; text-align:center; }}
        .stat .value {{ font-size:28px; font-weight:700; color:#ffffff; }}
        .stat .label {{ font-size:13px; color:#8899aa; margin-top:4px; }}
        .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; max-width:1200px; margin:0 auto; }}
        .card {{ background:#161b22; border:1px solid #21262d; border-radius:12px; padding:24px; }}
        .card h2 {{ font-size:16px; color:#ffffff; margin-bottom:16px; padding-bottom:10px; border-bottom:1px solid #1a2332; }}
        .full-width {{ grid-column:1/-1; }}
        table {{ width:100%; border-collapse:collapse; font-size:14px; }}
        th {{ text-align:left; color:#8899aa; font-weight:600; padding:8px 12px; border-bottom:1px solid #1a2332; }}
        td {{ padding:8px 12px; border-bottom:1px solid #0d1117; }}
        a {{ color:#7c8aff; text-decoration:none; }}
        a:hover {{ text-decoration:underline; }}
        @media (max-width:768px) {{
            .grid {{ grid-template-columns:1fr; }}
            .stat-row {{ flex-direction:column; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Trading Intelligence</h1>
        <div class="subtitle">Yuri — Market Monitor for Tony Gale</div>
        <div class="updated">Last updated: {updated}</div>
    </div>

    <div class="stat-row">
        <div class="stat">
            <div class="value">{len(latest)}</div>
            <div class="label">Tracked Symbols</div>
        </div>
        <div class="stat">
            <div class="value">{len([a for a in alerts if a.get('triggered')])}</div>
            <div class="label">Triggered Alerts</div>
        </div>
        <div class="stat">
            <div class="value">{len([s for s in latest_signals.values() if s.get('signal_changed')])}</div>
            <div class="label">Signal Changes</div>
        </div>
        <div class="stat">
            <div class="value">{auto_status_badge}</div>
            <div class="label">Auto-Trade ({len(auto_open)}/5 slots)</div>
        </div>
        <div class="stat">
            <div class="value" style="color:{auto_pnl_color}">${auto_daily_pnl:+.2f}</div>
            <div class="label">Auto-Trade Day P&L</div>
        </div>
        <div class="stat">
            <div class="value">{len([n for n in news if n.get('impact_level') == 'HIGH'])}</div>
            <div class="label">High Impact News</div>
        </div>
    </div>

    <div class="grid">
        {commentary_card}
        <div class="card full-width">
            <h2>Market Positions</h2>
            <table>
                <tr><th>Symbol</th><th>Price</th><th>Day %</th><th>Volume</th><th>Signal</th></tr>
                {positions_rows if positions_rows else '<tr><td colspan="5" style="color:#556677;text-align:center">No market data yet — waiting for Questrade API activation</td></tr>'}
            </table>
        </div>

        <div class="card">
            <h2>Price Alerts</h2>
            {f'<table><tr><th>Symbol</th><th>Type</th><th>Threshold</th><th>Status</th></tr>{alerts_rows}</table>' if alerts_rows else '<p style="color:#556677">No price alerts configured</p>'}
        </div>

        <div class="card">
            <h2>Signal Changes</h2>
            {signal_changes if signal_changes else '<p style="color:#556677">No recent signal changes</p>'}
        </div>

        <div class="card full-width">
            <h2>Auto-Trade Positions</h2>
            {f'<table><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry</th><th>Current</th><th>P&L</th><th>Flags</th></tr>{auto_positions_rows}</table>' if auto_positions_rows else '<p style="color:#556677">No open auto-trade positions</p>'}
        </div>

        <div class="card full-width">
            <h2>Recent Auto-Trades</h2>
            {f'<table><tr><th>Symbol</th><th>Side</th><th>Entry</th><th>Exit</th><th>P&L</th><th>Reason</th><th>Date</th></tr>{auto_closed_rows}</table>' if auto_closed_rows else '<p style="color:#556677">No closed auto-trades yet</p>'}
        </div>

        <div class="card full-width">
            <h2>Financial News</h2>
            {news_items if news_items else '<p style="color:#556677">No news articles yet</p>'}
        </div>

        <div class="card full-width">
            <h2>Social / Political Signals</h2>
            {social_items if social_items else '<p style="color:#556677">No social signals yet</p>'}
        </div>

        <div class="card">
            <h2>Watchlist</h2>
            <div style="display:flex;flex-wrap:wrap;gap:8px">
                {''.join(f'<span style="background:#21262d;padding:4px 12px;border-radius:6px;font-size:13px">{s}</span>' for s in watchlist) if watchlist else '<span style="color:#556677">No watchlist configured</span>'}
            </div>
        </div>
    </div>
</body>
</html>"""

    out = CANVAS_DIR / "trading.html"
    out.write_text(html)
    print(f"Dashboard generated: {out}")
    print(f"  Symbols: {len(latest)}, Alerts: {len(alerts)}, News: {len(news)}, Social: {len(social)}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    if sys.argv[1].lower() == "generate":
        cmd_generate()
    else:
        print(f"Unknown command: {sys.argv[1]}")
        print(__doc__)


if __name__ == "__main__":
    main()
