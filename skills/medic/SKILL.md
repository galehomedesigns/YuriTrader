---
name: medic
description: System health monitoring, diagnostics, and RAG logging. Checks cron jobs, Supabase connectivity, data freshness, auth tokens, and memory sync. Triggers on requests about system health, diagnostics, errors, logs, or monitoring.
---

# Medic Skill

System health checker and RAG event logger for the OpenClaw/Yuri infrastructure.

## Health Checks

```bash
python3 {baseDir}/scripts/medic.py check           # Run all 7 health checks
python3 {baseDir}/scripts/medic.py report           # Formatted Telegram report
python3 {baseDir}/scripts/medic.py dashboard        # Generate health.html
python3 {baseDir}/scripts/medic.py fix stale-locks  # Clear stale .lock files
python3 {baseDir}/scripts/medic.py fix memory-sync  # Force sync today's memory to DB
```

## RAG Event Logging

```bash
# Log a cron job result directly to conversation_log (with embedding)
python3 {baseDir}/scripts/log_event.py --source "trading-premarket" --summary "Briefing summary" --topics trading alerts

# Sync today's unlogged memory file to Supabase
python3 {baseDir}/scripts/log_event.py --backfill-today

# Backfill historical memory files
python3 {baseDir}/scripts/log_event.py --backfill-range 2026-03-01 2026-03-27
```

## What Gets Checked

1. **Cron Health** — consecutiveErrors, timeouts, missed runs
2. **Supabase Connectivity** — all 10 key tables accessible
3. **Data Freshness** — market_snapshots, news_events, social_signals have recent data
4. **Memory Sync** — today's conversation_log entry exists
5. **Questrade Auth** — token file valid and not expired
6. **Container Health** — healthcheck endpoint + uptime
7. **Trade Audit** — auto_trades and trade_audit consistency

## Supabase Table

`system_health_log` — stores all check results with timestamp, status (OK/WARN/FAIL), details, and recommendations.
