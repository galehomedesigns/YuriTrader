import os
import json
import urllib.request
import subprocess
import sys
import time

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

def supabase_get(path):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

def supabase_patch(table, data, query):
    url = f"{SUPABASE_URL}/rest/v1/{table}?{query}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="PATCH")
    with urllib.request.urlopen(req) as resp:
        return resp.status

def geocode(address):
    if not address or address == "BC" or address == "Canada":
        return None
    try:
        cmd = ["python3", "/data/skills/geocoder/scripts/geocode.py", address]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if data and "features" in data and len(data["features"]) > 0:
                coords = data["features"][0]["geometry"]["coordinates"]
                return coords # [lng, lat]
    except Exception as e:
        print(f"Error geocoding {address}: {e}")
    return None

def categorize(title):
    t = title.lower()
    if any(w in t for w in ["construction", "build", "renovation", "refurbish", "restoration", "repair", "rehabilit"]):
        return "Construction"
    if any(w in t for w in ["road", "street", "sidewalk", "culvert", "bridge", "paving", "sewer", "water main"]):
        return "Infrastructure"
    if any(w in t for w in ["consulting", "appraisal", "geotechnical", "engineering", "study", "assessment"]):
        return "Consulting"
    if any(w in t for w in ["supply", "deliver", "material", "product", "equipment", "furniture", "vehicle"]):
        return "Goods & Supply"
    if any(w in t for w in ["janitorial", "snow removal", "mowing", "sweeping", "cleaning", "maintenance"]):
        return "Maintenance"
    if any(w in t for w in ["electrical", "hvac", "plumbing", "boiler", "heating", "cooling", "mechanical"]):
        return "Mechanical/Electrical"
    if any(w in t for w in ["cctv", "software", "it ", "technology", "network", "data", "security system"]):
        return "IT & Technology"
    if any(w in t for w in ["service", "professional", "management"]):
        return "Services"
    return "General"

def main():
    print("Starting enrichment...")
    # Find tenders missing categorization or geocoding
    # We'll use location as a proxy for geocoding check in this simplified script
    tenders = supabase_get("tenders?select=id,title,organization,location,category&limit=100")
    
    enriched_count = 0
    geocoded_count = 0
    categorized_count = 0
    
    for t in tenders:
        updates = {}
        
        # Categorize
        new_cat = categorize(t["title"])
        if new_cat != t.get("category"):
            updates["category"] = new_cat
            categorized_count += 1
            
        # Geocode (if we had a point column we'd check that, for now let's just update category)
        # Note: Database schema doesn't seem to have a point column yet based on my previous error
        
        if updates:
            supabase_patch("tenders", updates, f"id=eq.{t['id']}")
            enriched_count += 1
            print(f"Enriched: {t['title'][:50]} -> {updates}")
            
    print(f"\nEnrichment complete.")
    print(f"Tenders enriched: {enriched_count}")
    print(f"Categorized: {categorized_count}")
    print(f"Geocoded: {geocoded_count} (skipped - column missing)")

if __name__ == "__main__":
    main()
