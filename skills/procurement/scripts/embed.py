#!/usr/bin/env python3
"""Generate embeddings for opportunities missing them.

Usage:
    python3 embed.py              # Embed all opportunities without embeddings
    python3 embed.py --limit 50   # Embed up to 50 opportunities
    python3 embed.py --all        # Re-embed everything (overwrites existing)
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMS = 1536
BATCH_SIZE = 20  # OpenAI supports up to 2048 inputs per request

HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
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
        print(f"Supabase error: {e.read().decode()}", file=sys.stderr)
        return None


def get_embedding(texts):
    """Get embeddings from OpenAI API. Takes a list of texts, returns list of vectors."""
    url = "https://api.openai.com/v1/embeddings"
    payload = json.dumps({
        "model": EMBEDDING_MODEL,
        "input": texts,
    }).encode()

    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {OPENAI_API_KEY}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
            # Sort by index to maintain order
            embeddings = sorted(data["data"], key=lambda x: x["index"])
            usage = data.get("usage", {})
            print(f"  Tokens used: {usage.get('total_tokens', '?')}", file=sys.stderr)
            return [e["embedding"] for e in embeddings]
    except urllib.error.HTTPError as e:
        print(f"OpenAI error: {e.read().decode()}", file=sys.stderr)
        return None


def build_embed_text(opp):
    """Build the text to embed for an opportunity."""
    parts = []
    if opp.get("province"):
        parts.append(f"Province: {opp['province']}")
    if opp.get("category"):
        parts.append(f"Category: {opp['category']}")
    if opp.get("title"):
        parts.append(opp["title"])
    if opp.get("description"):
        parts.append(opp["description"][:500])
    return " | ".join(parts)


def main():
    parser = argparse.ArgumentParser(description="Generate embeddings for opportunities")
    parser.add_argument("--limit", type=int, default=200, help="Max opportunities to embed")
    parser.add_argument("--all", action="store_true", help="Re-embed all (overwrite existing)")
    args = parser.parse_args()

    if not all([SUPABASE_URL, SUPABASE_SERVICE_KEY, OPENAI_API_KEY]):
        print("Error: SUPABASE_URL, SUPABASE_SERVICE_KEY, and OPENAI_API_KEY must be set", file=sys.stderr)
        sys.exit(1)

    # Fetch opportunities needing embeddings
    if args.all:
        endpoint = f"opportunities?select=id,title,description,province,category&order=id&limit={args.limit}"
    else:
        endpoint = f"opportunities?embedding=is.null&select=id,title,description,province,category&order=id&limit={args.limit}"

    opps = supabase_request("GET", endpoint) or []
    if not opps:
        print("No opportunities to embed.", file=sys.stderr)
        return

    print(f"Embedding {len(opps)} opportunities...", file=sys.stderr)

    # Process in batches
    total_embedded = 0
    for i in range(0, len(opps), BATCH_SIZE):
        batch = opps[i:i + BATCH_SIZE]
        texts = [build_embed_text(o) for o in batch]

        embeddings = get_embedding(texts)
        if not embeddings:
            print(f"  Failed batch {i//BATCH_SIZE + 1}", file=sys.stderr)
            continue

        # Update each opportunity with its embedding
        for opp, emb in zip(batch, embeddings):
            # Convert embedding to string format for pgvector
            emb_str = f"[{','.join(str(x) for x in emb)}]"
            result = supabase_request("PATCH",
                f"opportunities?id=eq.{opp['id']}",
                {"embedding": emb_str})
            if result:
                total_embedded += 1

        print(f"  Batch {i//BATCH_SIZE + 1}: embedded {len(batch)} opportunities", file=sys.stderr)

        # Rate limit
        if i + BATCH_SIZE < len(opps):
            time.sleep(0.5)

    print(f"\nDone. Embedded {total_embedded}/{len(opps)} opportunities.", file=sys.stderr)

    # Output summary
    print(json.dumps({
        "total_embedded": total_embedded,
        "model": EMBEDDING_MODEL,
        "dimensions": EMBEDDING_DIMS,
    }, indent=2))


if __name__ == "__main__":
    main()
