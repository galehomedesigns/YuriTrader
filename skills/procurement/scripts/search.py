#!/usr/bin/env python3
"""Search procurement opportunities using hybrid RAG.

Usage:
    python3 search.py "road paving contracts in BC"
    python3 search.py "IT services" --province ON --status open
    python3 search.py "construction" --category construction --limit 5
    python3 search.py --keyword "erosion control"
    python3 search.py --list                    # List all open opportunities
    python3 search.py --stats                   # Show database stats
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
}


def supabase_request(method, endpoint, data=None):
    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in HEADERS.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"Error: {e.read().decode()}", file=sys.stderr)
        return None


def supabase_rpc(fn_name, params):
    url = f"{SUPABASE_URL}/rest/v1/rpc/{fn_name}"
    body = json.dumps(params).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    for k, v in HEADERS.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"RPC error: {e.read().decode()}", file=sys.stderr)
        return None


def get_embedding(text):
    """Get embedding from OpenAI."""
    url = "https://api.openai.com/v1/embeddings"
    payload = json.dumps({
        "model": "text-embedding-3-small",
        "input": text,
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {OPENAI_API_KEY}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            return data["data"][0]["embedding"]
    except Exception as e:
        print(f"Embedding error: {e}", file=sys.stderr)
        return None


def hybrid_search(query, province=None, category=None, status="open", limit=20):
    """Run hybrid search (keyword + semantic + filters)."""
    embedding = get_embedding(query)
    if not embedding:
        print("Failed to generate query embedding, falling back to keyword search", file=sys.stderr)
        return keyword_search(query, province, category, status, limit)

    params = {
        "query_text": query,
        "query_embedding": f"[{','.join(str(x) for x in embedding)}]",
        "match_count": limit,
        "full_text_weight": 1.0,
        "semantic_weight": 1.5,
        "filter_province": province,
        "filter_category": category,
        "filter_status": status,
    }

    results = supabase_rpc("hybrid_search_opportunities", params)
    return results or []


def keyword_search(query, province=None, category=None, status="open", limit=20):
    """Fallback: keyword-only search using full-text search."""
    endpoint = f"opportunities?fts=wfts.{query}&select=id,title,description,province,category,deadline_date,source_url,status&limit={limit}&order=deadline_date"
    if province:
        endpoint += f"&province=eq.{province}"
    if category:
        endpoint += f"&category=eq.{category}"
    if status:
        endpoint += f"&status=eq.{status}"
    return supabase_request("GET", endpoint) or []


def list_opportunities(province=None, status="open", limit=50):
    """List all opportunities with filters."""
    endpoint = f"opportunities?select=id,title,province,category,deadline_date,source_url,status,organization_id&order=deadline_date&limit={limit}"
    if province:
        endpoint += f"&province=eq.{province}"
    if status:
        endpoint += f"&status=eq.{status}"
    return supabase_request("GET", endpoint) or []


def get_stats():
    """Get database statistics."""
    stats = {}

    # Total opportunities
    opps = supabase_request("GET", "opportunities?select=id&limit=1000") or []
    stats["total_opportunities"] = len(opps)

    # By status
    for s in ["open", "closed", "awarded", "cancelled"]:
        r = supabase_request("GET", f"opportunities?status=eq.{s}&select=id&limit=1000") or []
        stats[f"status_{s}"] = len(r)

    # By province
    for p in ["ON", "BC", "AB", "MB", "FED"]:
        r = supabase_request("GET", f"opportunities?province=eq.{p}&select=id&limit=1000") or []
        if len(r) > 0:
            stats[f"province_{p}"] = len(r)

    # With embeddings
    embedded = supabase_request("GET", "opportunities?embedding=not.is.null&select=id&limit=1000") or []
    stats["with_embeddings"] = len(embedded)

    # Organizations
    orgs = supabase_request("GET", "organizations?select=id&limit=100") or []
    stats["organizations"] = len(orgs)

    return stats


def format_results(results):
    """Format results for display."""
    if not results:
        print("No results found.")
        return

    for i, r in enumerate(results, 1):
        title = r.get("title", "N/A")
        province = r.get("province", "?")
        category = r.get("category", "?")
        deadline = r.get("deadline_date", "N/A")
        url = r.get("source_url", "")
        status = r.get("status", "?")
        score = r.get("rank_score", "")
        org = r.get("organization_name", "")

        print(f"\n{i}. {title}")
        print(f"   Province: {province} | Category: {category} | Status: {status}")
        if deadline:
            print(f"   Deadline: {deadline}")
        if org:
            print(f"   Organization: {org}")
        if score:
            print(f"   Relevance: {score:.4f}")
        if url:
            print(f"   URL: {url}")


def main():
    parser = argparse.ArgumentParser(description="Search procurement opportunities")
    parser.add_argument("query", nargs="*", help="Search query")
    parser.add_argument("--province", help="Filter by province code (ON, BC, AB, MB, FED)")
    parser.add_argument("--category", help="Filter by category (goods, services, construction)")
    parser.add_argument("--status", default="open", help="Filter by status (default: open)")
    parser.add_argument("--limit", type=int, default=20, help="Max results")
    parser.add_argument("--keyword", help="Keyword-only search (no semantic)")
    parser.add_argument("--list", action="store_true", help="List all opportunities")
    parser.add_argument("--stats", action="store_true", help="Show database stats")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set", file=sys.stderr)
        sys.exit(1)

    if args.stats:
        stats = get_stats()
        if args.json:
            print(json.dumps(stats, indent=2))
        else:
            print("\n📊 Procurement Database Stats")
            print("=" * 40)
            for k, v in stats.items():
                label = k.replace("_", " ").title()
                print(f"  {label}: {v}")
        return

    if args.list:
        results = list_opportunities(args.province, args.status, args.limit)
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            format_results(results)
        return

    if args.keyword:
        results = keyword_search(args.keyword, args.province, args.category, args.status, args.limit)
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            format_results(results)
        return

    query = " ".join(args.query) if args.query else None
    if not query:
        parser.print_help()
        sys.exit(1)

    if not OPENAI_API_KEY:
        print("Warning: OPENAI_API_KEY not set, using keyword search only", file=sys.stderr)
        results = keyword_search(query, args.province, args.category, args.status, args.limit)
    else:
        results = hybrid_search(query, args.province, args.category, args.status, args.limit)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        format_results(results)


if __name__ == "__main__":
    main()
