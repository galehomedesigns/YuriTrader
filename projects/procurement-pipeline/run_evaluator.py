import json
import os
import subprocess

def run_evaluation():
    print("Fetching tenders from database...")
    res = subprocess.run(["python3", "/data/skills/procurement/scripts/crawl.py", "--stats"], capture_output=True, text=True)
    try:
        data = json.loads(res.stdout)
        tenders = data.get("tenders", [])
    except:
        print("Error parsing tender data.")
        return

    results = {
        "total": len(tenders),
        "industrial": 0,
        "supply": 0,
        "noise": 0,
        "login_required": 0
    }

    # Simplified mock logic for categorization matching the new AI evaluator instructions
    industrial_keywords = ["industrial", "oil", "gas", "pipeline", "infrastructure", "mechanical", "electrical", "construction", "treatment", "power", "utility", "sewer", "water", "condenser", "fermenter", "boiler", "pressure", "piping", "road", "paving", "bridge", "erosion", "rehabilitation", "redevelopment"]
    supply_keywords = ["supply", "deliver", "equipment", "hardware", "material", "furniture", "office", "supplies", "parts", "sand", "gravel", "apparel", "ppe", "breathing apparatus", "vehicle", "lumber"]

    for t in tenders:
        title = t["title"].lower()
        cat = t.get("category", "").lower()
        
        if any(p in t["url"].lower() for p in ["bcbid", "bonfire", "bidsandtenders", "sciquest", "civicinfo"]):
            results["login_required"] += 1

        is_industrial = any(k in title for k in industrial_keywords) or cat in ["construction", "infrastructure", "mechanical/electrical", "engineering"]
        is_supply = any(k in title for k in supply_keywords) or cat in ["goods & supply", "it & technology"]

        if is_industrial:
            results["industrial"] += 1
        elif is_supply:
            results["supply"] += 1
        else:
            # Everything else not strictly industrial or supply falls into "noise" (e.g. consulting, insurance, services)
            if cat in ["consulting", "services", "maintenance", "general"] and not "supply" in title:
                results["noise"] += 1
            else:
                results["supply"] += 1 # Defaulting general goods to supply

    print("\n📊 Procurement Pipeline Report — March 22, 2026 (Updated)")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"🔍 Crawler: {len(data.get('sources', []))} sources scanned")
    print(f"🏗️ Industrial Projects: {results['industrial']}")
    print(f"📦 Material/Supply Procurement: {results['supply']}")
    print(f"🗑️ Filtered Noise (Consulting/Services/News): {results['noise']}")
    print(f"🔑 Access: {results['login_required']} tenders flagged for Sign-In Service")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

run_evaluation()
