import os
import json
import subprocess
import time
import urllib.request
import sys
from datetime import datetime

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

def supabase_get(path):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"Error fetching from Supabase: {e}")
        return []

def supabase_patch(table, data, query):
    url = f"{SUPABASE_URL}/rest/v1/{table}?{query}"
    headers = {**HEADERS, "Prefer": "return=minimal"}
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="PATCH")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except Exception as e:
        print(f"Error patching Supabase: {e}")
        return None

def main():
    print("Fetching project tasks...")
    tasks = supabase_get("project_tasks?select=*&limit=10")
    if tasks:
        for t in tasks:
            print(f"- [{t.get('status')}] {t.get('task_name')}")
    else:
        print("No tasks found.")
    return

    industrial_keywords = ["industrial", "oil", "gas", "pipeline", "infrastructure", "mechanical", "electrical", "construction", "treatment", "power", "utility", "sewer", "water", "condenser", "fermenter", "boiler", "pressure", "piping", "road", "paving", "bridge", "erosion", "rehabilitation", "redevelopment", "dike", "drainage", "structural", "demolition"]
    supply_keywords = ["supply", "deliver", "equipment", "hardware", "material", "furniture", "office", "supplies", "parts", "sand", "gravel", "apparel", "ppe", "breathing apparatus", "vehicle", "lumber", "playground", "bus"]

    results = {"relevant": 0, "noise": 0, "login": 0}

    for t in tenders:
        title = t["title"].lower()
        cat = (t.get("category") or "").lower()
        url = (t.get("url") or "").lower()
        
        is_relevant = False
        requires_login = False
        
        # Login detection
        if any(p in url for p in ["bcbid", "bonfire", "bidsandtenders", "sciquest", "civicinfo", "merx"]):
            requires_login = True
            results["login"] += 1

        # Relevance detection
        is_industrial = any(k in title for k in industrial_keywords) or cat in ["construction", "infrastructure", "mechanical/electrical", "engineering"]
        is_supply = any(k in title for k in supply_keywords) or cat in ["goods & supply", "it & technology"]

        if is_industrial:
            is_relevant = True
            results["relevant"] += 1
        elif is_supply:
            is_relevant = True # We keep supply as per instructions: "Do NOT reject supply tenders"
            results["relevant"] += 1
        else:
            if cat in ["consulting", "services", "maintenance", "general"] and not any(k in title for k in industrial_keywords + supply_keywords):
                is_relevant = False
                results["noise"] += 1
            else:
                is_relevant = True
                results["relevant"] += 1

        supabase_patch("tenders", {"is_relevant": is_relevant, "requires_login": requires_login, "confidence_score": 0.9}, f"id=eq.{t['id']}")
        print(f"Evaluated: {t['title'][:50]} - Relevant: {is_relevant}")
        time.sleep(0.2)

    print(f"\nEvaluation Complete:")
    print(f"Total: {len(tenders)}")
    print(f"Relevant: {results['relevant']}")
    print(f"Noise: {results['noise']}")
    print(f"Login Required: {results['login']}")

if __name__ == "__main__":
    main()
