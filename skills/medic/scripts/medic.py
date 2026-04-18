#!/usr/bin/env python3
"""
Medic Agent — system health checker for OpenClaw/Yuri.

Usage:
    python3 medic.py check              # Run all health checks
    python3 medic.py report             # Run checks + Telegram-formatted summary
    python3 medic.py fix stale-locks    # Clear stale lock files
    python3 medic.py fix memory-sync    # Force sync today's memory to Supabase
    python3 medic.py dashboard          # Generate health.html
"""

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
CRON_FILE = Path("/data/.openclaw/cron/jobs.json")
MEMORY_DIR = Path("/data/.openclaw/workspace/memory")
TOKEN_FILE = Path("/home/tonygale/openclaw/state/questrade_token.json")
CANVAS_DIR = Path("/data/.openclaw/canvas")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

NOW = datetime.now(timezone.utc)
RUN_ID = str(uuid.uuid4())


def supabase_get(table, params=None):
    try:
        resp = httpx.get(f"{SUPABASE_URL}/rest/v1/{table}",
            headers={**HEADERS, "Prefer": "return=representation"},
            params=params or {}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return None


def supabase_post(table, data):
    try:
        resp = httpx.post(f"{SUPABASE_URL}/rest/v1/{table}",
            headers={**HEADERS, "Prefer": "return=minimal"},
            json=data, timeout=10)
        return resp.status_code in (200, 201)
    except Exception:
        return False


def log_check(check_name, status, details=None, recommendation=None):
    supabase_post("system_health_log", {
        "check_name": check_name,
        "status": status,
        "details": details or {},
        "recommendation": recommendation,
        "run_id": RUN_ID,
    })
    return {"check": check_name, "status": status, "details": details, "rec": recommendation}


# ── Health Checks ──

def check_cron_health():
    """Check all cron jobs for errors and missed runs."""
    results = []
    if not CRON_FILE.exists():
        results.append(log_check("cron.config", "FAIL", {"error": "jobs.json not found"},
            "Cron config file missing at /data/.openclaw/cron/jobs.json"))
        return results

    data = json.loads(CRON_FILE.read_text())
    for job in data.get("jobs", []):
        if not job.get("enabled"):
            continue

        name = job.get("name", "unknown")
        state = job.get("state", {})
        errors = state.get("consecutiveErrors", 0)
        last_status = state.get("lastStatus", "unknown")
        last_error = state.get("lastError", "")
        duration = state.get("lastDurationMs", 0)
        timeout = job.get("payload", {}).get("timeoutSeconds", 0) * 1000

        if errors > 0 or last_status == "error":
            rec = f"Job '{name}' has {errors} consecutive errors. Last error: {last_error}"
            if "timeout" in str(last_error).lower() and timeout > 0:
                rec += f". Consider increasing timeout from {timeout//1000}s to {timeout//500}s."
            results.append(log_check(f"cron.{name}", "FAIL",
                {"errors": errors, "lastError": last_error, "durationMs": duration}, rec))
        elif duration > 0 and timeout > 0 and duration > timeout * 0.8:
            results.append(log_check(f"cron.{name}", "WARN",
                {"durationMs": duration, "timeoutMs": timeout},
                f"Job '{name}' used {duration/timeout*100:.0f}% of its timeout budget."))
        else:
            results.append(log_check(f"cron.{name}", "OK",
                {"durationMs": duration, "lastStatus": last_status}))

    return results


def check_supabase():
    """Check connectivity to all key tables."""
    tables = ["conversation_log", "project_tasks", "market_snapshots", "news_events",
              "social_signals", "auto_trades", "trade_audit", "tenders", "trading_config", "system_health_log"]
    results = []
    for table in tables:
        data = supabase_get(table, {"select": "count", "limit": "1"})
        if data is None:
            results.append(log_check(f"supabase.{table}", "FAIL",
                {"error": "Connection failed"}, f"Cannot access table '{table}'. Check Supabase connectivity."))
        else:
            results.append(log_check(f"supabase.{table}", "OK", {"accessible": True}))
    return results


def check_data_freshness():
    """Check that key tables have recent data."""
    results = []
    checks = {
        "market_snapshots": {"field": "snapshot_at", "max_age_hours": 24, "note": "Market hours only"},
        "news_events": {"field": "fetched_at", "max_age_hours": 2},
        "social_signals": {"field": "fetched_at", "max_age_hours": 2},
    }

    for table, cfg in checks.items():
        rows = supabase_get(table, {
            "select": cfg["field"],
            "order": f"{cfg['field']}.desc",
            "limit": "1",
        })
        if not rows:
            results.append(log_check(f"freshness.{table}", "WARN",
                {"empty": True}, f"Table '{table}' has no data yet."))
            continue

        latest = rows[0][cfg["field"]]
        if latest:
            age = NOW - datetime.fromisoformat(latest.replace("Z", "+00:00"))
            hours = age.total_seconds() / 3600
            max_h = cfg["max_age_hours"]
            if hours > max_h:
                results.append(log_check(f"freshness.{table}", "WARN",
                    {"latest": latest, "age_hours": round(hours, 1), "threshold": max_h},
                    f"Data in '{table}' is {hours:.1f}h old (threshold: {max_h}h). {cfg.get('note', '')}"))
            else:
                results.append(log_check(f"freshness.{table}", "OK",
                    {"latest": latest, "age_hours": round(hours, 1)}))

    return results


def check_memory_sync():
    """Check if today's conversation log entry exists."""
    today = NOW.strftime("%Y-%m-%d")
    rows = supabase_get("conversation_log", {
        "session_date": f"eq.{today}",
        "select": "id",
        "limit": "1",
    })

    # Check if memory file exists for today
    mem_file = MEMORY_DIR / f"{today}.md"
    has_file = mem_file.exists()

    if rows:
        return [log_check("memory.sync", "OK", {"date": today, "synced": True, "file_exists": has_file})]
    elif has_file:
        return [log_check("memory.sync", "WARN",
            {"date": today, "synced": False, "file_exists": True},
            "Memory file exists but not synced to Supabase. Run: medic.py fix memory-sync")]
    else:
        return [log_check("memory.sync", "OK", {"date": today, "synced": False, "file_exists": False,
            "note": "No memory file for today — normal if no conversations yet."})]


def check_questrade():
    """Check Questrade token validity."""
    if not TOKEN_FILE.exists():
        return [log_check("questrade.auth", "WARN",
            {"token_file": False},
            "No cached token. Will use refresh token on next API call.")]

    try:
        data = json.loads(TOKEN_FILE.read_text())
        expires = data.get("expires_at", 0)
        import time
        remaining = expires - time.time()
        if remaining < 0:
            return [log_check("questrade.auth", "FAIL",
                {"expired": True, "expired_ago_min": round(-remaining/60)},
                "Questrade token expired. Generate new refresh token at questrade.com > Settings > API centre.")]
        elif remaining < 7200:
            return [log_check("questrade.auth", "WARN",
                {"expires_in_min": round(remaining/60)},
                "Questrade token expires soon. Will auto-refresh on next API call.")]
        else:
            return [log_check("questrade.auth", "OK",
                {"expires_in_min": round(remaining/60)})]
    except Exception as e:
        return [log_check("questrade.auth", "WARN", {"error": str(e)[:100]})]


def check_container():
    """Check container health endpoint and uptime."""
    results = []
    token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = httpx.get("http://127.0.0.1:18789/__openclaw__/canvas/", headers=headers, timeout=5)
        if resp.status_code == 200:
            results.append(log_check("container.health", "OK", {"status": 200}))
        else:
            results.append(log_check("container.health", "WARN",
                {"status": resp.status_code}, "Healthcheck returned non-200."))
    except Exception:
        results.append(log_check("container.health", "FAIL",
            {"error": "Healthcheck unreachable"}, "Container healthcheck endpoint not responding."))

    # Check uptime
    try:
        uptime = float(Path("/proc/uptime").read_text().split()[0])
        if uptime < 600:
            results.append(log_check("container.uptime", "WARN",
                {"uptime_min": round(uptime/60, 1)},
                "Container restarted recently (< 10 min ago)."))
        else:
            results.append(log_check("container.uptime", "OK",
                {"uptime_hours": round(uptime/3600, 1)}))
    except Exception:
        pass

    return results


def check_dashboard_access():
    """Check that dashboards are accessible: directory perms + Caddy serving."""
    results = []
    openclaw_dir = Path("/data/.openclaw")
    canvas_dir = CANVAS_DIR

    # Check .openclaw directory has o+x so Caddy can traverse into canvas/
    if openclaw_dir.exists():
        mode = openclaw_dir.stat().st_mode
        has_other_exec = bool(mode & 0o001)
        if not has_other_exec:
            # Auto-fix: add o+x
            try:
                os.chmod(str(openclaw_dir), mode | 0o001)
                results.append(log_check("dashboard.dir_perms", "WARN",
                    {"fixed": True, "dir": str(openclaw_dir)},
                    "Fixed: .openclaw dir was missing o+x (Caddy couldn't serve dashboards). Auto-repaired."))
            except Exception as e:
                results.append(log_check("dashboard.dir_perms", "FAIL",
                    {"error": str(e)},
                    f"Cannot fix .openclaw dir permissions. Run: chmod o+x {openclaw_dir}"))
        else:
            results.append(log_check("dashboard.dir_perms", "OK", {"dir": str(openclaw_dir)}))

    # Check canvas files exist and are readable
    if canvas_dir.exists():
        html_files = list(canvas_dir.glob("*.html"))
        if not html_files:
            results.append(log_check("dashboard.files", "WARN",
                {"count": 0}, "No HTML dashboards found in canvas directory."))
        else:
            results.append(log_check("dashboard.files", "OK", {"count": len(html_files)}))
    else:
        results.append(log_check("dashboard.files", "FAIL",
            {"error": "Canvas directory missing"}, f"Create: mkdir -p {canvas_dir}"))

    # Check Caddy can serve a dashboard via HTTPS
    try:
        resp = httpx.get("https://187-77-193-40.sslip.io/health.html",
            auth=("tony", "decades2026"), timeout=10, verify=False)
        if resp.status_code == 200:
            results.append(log_check("dashboard.caddy", "OK", {"status": 200}))
        elif resp.status_code == 403:
            results.append(log_check("dashboard.caddy", "FAIL",
                {"status": 403},
                "Caddy returns 403. Check dir permissions: chmod o+x /data/.openclaw"))
        elif resp.status_code == 401:
            results.append(log_check("dashboard.caddy", "WARN",
                {"status": 401}, "Caddy auth rejected. Password hash may be stale."))
        else:
            results.append(log_check("dashboard.caddy", "WARN",
                {"status": resp.status_code}, "Unexpected status from Caddy."))
    except Exception as e:
        results.append(log_check("dashboard.caddy", "WARN",
            {"error": str(e)[:100]}, "Could not reach Caddy HTTPS endpoint."))

    return results


def check_trade_audit():
    """Check auto_trades and trade_audit tables."""
    trades = supabase_get("auto_trades", {"select": "id", "limit": "1"})
    audit = supabase_get("trade_audit", {"select": "id", "limit": "1"})

    if trades and not audit:
        return [log_check("trading.audit", "WARN",
            {"auto_trades": True, "trade_audit_empty": True},
            "auto_trades has data but trade_audit is empty. Verify audit logging is working.")]

    return [log_check("trading.audit", "OK",
        {"auto_trades": bool(trades), "trade_audit": bool(audit)})]


# ── Commands ──

def cmd_check():
    """Run all health checks."""
    all_results = []
    all_results.extend(check_cron_health())
    all_results.extend(check_supabase())
    all_results.extend(check_data_freshness())
    all_results.extend(check_memory_sync())
    all_results.extend(check_questrade())
    all_results.extend(check_container())
    all_results.extend(check_dashboard_access())
    all_results.extend(check_trade_audit())

    ok = len([r for r in all_results if r["status"] == "OK"])
    warn = len([r for r in all_results if r["status"] == "WARN"])
    fail = len([r for r in all_results if r["status"] == "FAIL"])

    print(f"Health Check: OK({ok}) WARN({warn}) FAIL({fail})")
    for r in all_results:
        if r["status"] != "OK":
            print(f"  [{r['status']}] {r['check']}: {r.get('rec', '')}")

    return all_results


def cmd_report():
    """Run checks and format Telegram report."""
    results = cmd_check()
    ok = len([r for r in results if r["status"] == "OK"])
    warn = len([r for r in results if r["status"] == "WARN"])
    fail = len([r for r in results if r["status"] == "FAIL"])

    date = NOW.strftime("%b %d, %Y")
    lines = [f"YURI HEALTH REPORT — {date}"]
    lines.append(f"OK ({ok}) | WARN ({warn}) | FAIL ({fail})")

    fails = [r for r in results if r["status"] == "FAIL"]
    warns = [r for r in results if r["status"] == "WARN"]

    if fails:
        lines.append("\nFAIL:")
        for r in fails:
            lines.append(f"  {r['check']}")
            if r.get("rec"):
                lines.append(f"    {r['rec'][:120]}")

    if warns:
        lines.append("\nWARN:")
        for r in warns:
            lines.append(f"  {r['check']}")
            if r.get("rec"):
                lines.append(f"    {r['rec'][:120]}")

    if not fails and not warns:
        lines.append("\nAll systems healthy.")

    lines.append(f"\nDashboard: https://187-77-193-40.sslip.io/health.html")
    print("\n".join(lines))


def cmd_fix(issue):
    """Auto-fix known issues."""
    if issue == "stale-locks":
        result = subprocess.run(
            ["find", "/data/.openclaw", "-name", "*.lock", "-delete"],
            capture_output=True, text=True)
        print("Stale locks cleared.")

    elif issue == "memory-sync":
        script = "/home/tonygale/openclaw/skills/medic/scripts/log_event.py"
        result = subprocess.run(
            ["python3", script, "--backfill-today"],
            capture_output=True, text=True, timeout=60)
        print(result.stdout)
        if result.stderr:
            print(f"Errors: {result.stderr[:200]}")

    else:
        print(f"Unknown fix: {issue}")
        print("Available: stale-locks, memory-sync")


def cmd_dashboard():
    """Generate health.html dashboard."""
    # Get recent health data
    recent = supabase_get("system_health_log", {
        "timestamp": f"gte.{(NOW - timedelta(hours=24)).isoformat()}",
        "select": "*",
        "order": "timestamp.desc",
        "limit": "100",
    }) or []

    # Get latest per check
    latest = {}
    for r in recent:
        if r["check_name"] not in latest:
            latest[r["check_name"]] = r

    # Read cron state
    cron_data = []
    if CRON_FILE.exists():
        jobs = json.loads(CRON_FILE.read_text())
        for job in jobs.get("jobs", []):
            if not job.get("enabled"):
                continue
            state = job.get("state", {})
            cron_data.append({
                "name": job.get("name", "?"),
                "last_run": state.get("lastRunAtMs", 0),
                "duration": state.get("lastDurationMs", 0),
                "status": state.get("lastStatus", "?"),
                "errors": state.get("consecutiveErrors", 0),
                "last_error": state.get("lastError", ""),
            })

    ok_count = len([r for r in latest.values() if r["status"] == "OK"])
    warn_count = len([r for r in latest.values() if r["status"] == "WARN"])
    fail_count = len([r for r in latest.values() if r["status"] == "FAIL"])
    total = ok_count + warn_count + fail_count
    updated = NOW.strftime("%B %d, %Y at %I:%M %p UTC")

    # Cron table rows
    cron_rows = ""
    for c in cron_data:
        last = datetime.fromtimestamp(c["last_run"]/1000, tz=timezone.utc).strftime("%b %d %H:%M") if c["last_run"] else "Never"
        dur = f"{c['duration']/1000:.1f}s" if c["duration"] else "—"
        color = "#ff1744" if c["errors"] > 0 else "#00c853"
        badge = f'<span style="color:{color}">{c["status"]}</span>'
        err_text = f'<span style="color:#ff1744;font-size:11px">{c["last_error"][:60]}</span>' if c["errors"] > 0 else ""
        cron_rows += f"<tr><td>{c['name']}</td><td>{last}</td><td>{dur}</td><td>{badge}</td><td>{c['errors']}</td><td>{err_text}</td></tr>"

    # Non-OK events
    events = ""
    non_ok = [r for r in recent if r["status"] != "OK"][:20]
    for r in non_ok:
        ts = r["timestamp"][:16].replace("T", " ")
        scolor = "#ff1744" if r["status"] == "FAIL" else "#ffc107"
        badge = f'<span style="background:{scolor}22;color:{scolor};padding:2px 8px;border-radius:4px;font-size:11px">{r["status"]}</span>'
        rec = r.get("recommendation", "") or ""
        events += f'<div style="padding:8px 0;border-bottom:1px solid #1a2332">{badge} <strong>{r["check_name"]}</strong> <span style="color:#556677;font-size:12px">— {ts}</span><br><span style="color:#8899aa;font-size:12px">{rec[:150]}</span></div>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>System Health — Yuri</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0d1117;color:#ccd6dd;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;padding:20px}}
.header{{text-align:center;padding:30px 0 20px;border-bottom:1px solid #1a2332;margin-bottom:30px}}
.header h1{{font-size:28px;color:#fff;font-weight:700}}
.header .subtitle{{color:#8899aa;font-size:14px;margin-top:6px}}
.header .updated{{color:#556677;font-size:12px;margin-top:4px}}
.stat-row{{display:flex;gap:16px;margin-bottom:24px;max-width:1200px;margin:0 auto 24px}}
.stat{{flex:1;background:#161b22;border:1px solid #21262d;border-radius:12px;padding:20px;text-align:center}}
.stat .value{{font-size:28px;font-weight:700;color:#fff}}
.stat .label{{font-size:13px;color:#8899aa;margin-top:4px}}
.grid{{display:grid;grid-template-columns:1fr;gap:24px;max-width:1200px;margin:0 auto}}
.card{{background:#161b22;border:1px solid #21262d;border-radius:12px;padding:24px}}
.card h2{{font-size:16px;color:#fff;margin-bottom:16px;padding-bottom:10px;border-bottom:1px solid #1a2332}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th{{text-align:left;color:#8899aa;font-weight:600;padding:8px 12px;border-bottom:1px solid #1a2332}}
td{{padding:8px 12px;border-bottom:1px solid #0d1117}}
@media(max-width:768px){{.stat-row{{flex-direction:column}}}}
</style>
</head>
<body>
<div class="header">
<h1>System Health</h1>
<div class="subtitle">Yuri — OpenClaw Infrastructure Monitor</div>
<div class="updated">Last updated: {updated}</div>
</div>
<div class="stat-row">
<div class="stat"><div class="value" style="color:#00c853">{ok_count}</div><div class="label">OK</div></div>
<div class="stat"><div class="value" style="color:#ffc107">{warn_count}</div><div class="label">WARN</div></div>
<div class="stat"><div class="value" style="color:#ff1744">{fail_count}</div><div class="label">FAIL</div></div>
<div class="stat"><div class="value">{len(cron_data)}</div><div class="label">Cron Jobs</div></div>
</div>
<div class="grid">
<div class="card">
<h2>Cron Job Status</h2>
<table><tr><th>Job</th><th>Last Run</th><th>Duration</th><th>Status</th><th>Errors</th><th>Details</th></tr>
{cron_rows if cron_rows else '<tr><td colspan="6" style="color:#556677">No cron data</td></tr>'}
</table>
</div>
<div class="card">
<h2>Recent Issues (24h)</h2>
{events if events else '<p style="color:#556677">No issues in the last 24 hours</p>'}
</div>
</div>
</body>
</html>"""

    out = CANVAS_DIR / "health.html"
    out.write_text(html)
    # Fix ownership
    try:
        import pwd
        uid = pwd.getpwnam("ubuntu").pw_uid
        gid = pwd.getpwnam("ubuntu").pw_gid
        os.chown(str(out), uid, gid)
    except Exception:
        os.system(f"chmod 644 {out}")  # fallback
    print(f"Dashboard generated: {out}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1].lower()
    if cmd == "check":
        cmd_check()
    elif cmd == "report":
        cmd_report()
    elif cmd == "fix":
        if len(sys.argv) < 3:
            print("Usage: medic.py fix <stale-locks|memory-sync>")
            return
        cmd_fix(sys.argv[2])
    elif cmd == "dashboard":
        cmd_dashboard()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
