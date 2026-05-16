**Status:** Archived 2026-04-23 — resumable. Crawl cron disabled; code + Supabase `tenders` table intact. See crontab comment for revival.

---
name: procurement
description: BC procurement intelligence aggregator. Crawls 10 municipal and provincial tender sources, stores in Supabase with pgvector embeddings for RAG search. Triggers on requests about tenders, RFPs, procurement, bids, or opportunities.
---

# Procurement Intelligence

Aggregates BC procurement opportunities from 10 sources into Supabase with vector embeddings for RAG search.

## Quick Commands

```bash
# Crawl all sources
python3 {baseDir}/scripts/crawl.py

# Crawl specific source by slug
python3 {baseDir}/scripts/crawl.py --source civicinfo-bc

# Dry run (parse but don't save)
python3 {baseDir}/scripts/crawl.py --dry-run

# Generate embeddings for tenders missing them
python3 {baseDir}/scripts/crawl.py --embed

# Get stats as JSON (feeds the dashboard)
python3 {baseDir}/scripts/crawl.py --stats
```

## Dashboard

Live dashboard at: `/__openclaw__/canvas/procurement.html`
Fetches directly from Supabase — always current.

## Business Purpose

The procurement database is the **top of the funnel** for The Project Wheel SaaS platform:
1. **Free tender alerts** (newsletters, social posts) attract construction managers, estimators, and schedulers
2. **Procurement dashboard** builds trust and provides free value
3. **CTA → The Project Wheel** — users who find tenders need tools to respond (schedules, estimates, risk analysis)
4. **Paid SaaS conversion** — The Project Wheel handles the full project lifecycle from bid to execution

Every tender in this database is a potential lead for Project Wheel licensing.

## Sources (Phase 1: BC + National Aggregator)

| # | Source | Slug | Platform | Region | Type |
|---|--------|------|----------|--------|------|
| 1 | CivicInfo BC | civicinfo-bc | civicinfo | Province-wide | Direct |
| 2 | Metro Vancouver | metro-vancouver | bidsandtenders | Metro Vancouver | Direct |
| 3 | City of Vancouver | city-vancouver | sciquest | Metro Vancouver | Direct |
| 4 | City of Surrey | city-surrey | bidsandtenders | Metro Vancouver | Direct |
| 5 | City of Victoria | city-victoria | bonfire | Vancouver Island | Direct |
| 6 | City of Kelowna | city-kelowna | bonfire | Okanagan | Direct |
| 7 | City of Nanaimo | city-nanaimo | bidsandtenders | Vancouver Island | Direct |
| 8 | City of Prince George | city-prince-george | bidsandtenders | Northern BC | Direct |
| 9 | City of Richmond | city-richmond | custom | Metro Vancouver | Direct |
| 10 | BC Bid | bc-bid | bcbid | Province-wide | Direct |
| 11 | ConstructConnect (DCN) | constructconnect | constructconnect | National | Aggregator |

### Source Types
- **Direct**: Government/municipal websites that publish their own tenders. These are the original authoritative sources.
- **Aggregator**: Third-party sites that collect and list tenders from multiple sources (like ConstructConnect/DCN).

### Adding Sources via Email Notification
Some sources (e.g., BC Bid) have built-in notification systems. Instead of scraping:
1. Register with the source using decadesdevelopments@gmail.com
2. Set up a Gmail filter to forward notifications to tonygale@agentmail.to
3. Yuri parses the forwarded emails via the AgentMail skill and loads into Supabase

## Database Schema (Supabase)

- **procurement_sources** — source registry (name, URL, platform, region, access method)
- **tenders** — tender listings with pgvector embeddings (1536-dim, text-embedding-3-small)
- **match_tenders()** — RPC function for RAG semantic search

## RAG Search

Use the Supabase skill's vector-search command:
```bash
{baseDir}/../supabase/scripts/supabase.sh vector-search tenders "road construction BC" --match-fn match_tenders --limit 10
```

## Data Collection Model (Google News Approach)

This system follows the same model Google News uses for aggregating content:

1. **Index public factual data** — tender titles, bid dates, reference numbers, and issuing organizations are factual data not subject to copyright under Canadian or US law.
2. **Link to original source** — always store and display the original tender URL. Users click through to the authoritative source for full details.
3. **Multi-source verification** — when a tender appears on both an aggregator (ConstructConnect) AND the original municipal site, store both as independent references. This strengthens the database.
4. **No reproduction of copyrightable content** — we don't copy full tender descriptions, proprietary analysis, or formatted presentations from aggregator sites. Only factual metadata.

### What's clearly legal
- Scraping government/municipal websites — public records published to attract bidders
- Indexing factual metadata (title, date, org, URL) from any public listing
- Linking to original sources

### What to avoid
- Reproducing ConstructConnect's proprietary categorization or editorial content
- Bulk-downloading paywalled tender documents
- Circumventing login/authentication barriers
- Violating robots.txt directives

## Environment Variables Required

- `SUPABASE_URL` — Supabase project URL
- `SUPABASE_SERVICE_KEY` — Service role key
- `OPENAI_API_KEY` — For text-embedding-3-small embeddings
- `FIRECRAWL_API_KEY` — For web crawling

## Cost

- Supabase: Free tier ($0)
- Embeddings: ~$0.01 per 500 tenders
- Firecrawl: ~1 credit per page crawl (500K free credits)
