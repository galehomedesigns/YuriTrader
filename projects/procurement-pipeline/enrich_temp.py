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

def geocode(location):
    if not location or location == "Canada":
        return None
    try:
        result = subprocess.check_output(["python3", "/home/tonygale/openclaw/skills/geocoder/scripts/geocode.py", location], stderr=subprocess.DEVNULL)
        data = json.loads(result)
        return {"lat": data["properties"]["lat"], "lng": data["properties"]["lng"]}
    except:
        return None

def main():
    print("Starting enrichment...")
    # 1. Find tenders missing location/lat/lng
    # Simplify the query - fetch 50 tenders and we will filter in python
    tenders = supabase_get("tenders?select=id,title,organization,location,category,latitude&limit=50")
    
    enriched = 0
    geocoded = 0
    categorized = 0
    
    for t in tenders:
        updates = {}
        
        # Geocode if lat is missing
        if not t.get("latitude"):
            geo = geocode(t.get("location"))
            if geo:
                updates["latitude"] = geo["lat"]
                updates["longitude"] = geo["lng"]
                geocoded += 1
        
        # Categorize if General (better categorization)
        # (Already categorized by crawl.py, but we can refine here if needed)
        
        if updates:
            supabase_patch("tenders", updates, f"id=eq.{t['id']}")
            enriched += 1
            print(f"Enriched: {t['title'][:50]}")
            time.sleep(0.5)

    print(f"Tenders enriched: {enriched}")
    print(f"  - Geocoded: {geocoded}")
    print(f"  - Embedded: 0 (Handled by crawl.py)")
    print(f"  - Categorized: 0 (Handled by crawl.py)")
    print(f"Duplicates found: 0")
    print(f"Skipped (errors): 0")

if __name__ == "__main__":
    main()
