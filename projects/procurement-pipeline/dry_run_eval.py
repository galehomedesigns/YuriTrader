import json
import os

tenders = [
    {"id":5,"title":"26-05-INF Kal Tire Place Condenser Replacement","type":"Request for Quotations","org":"Vernon","url":"https://www.civicinfo.bc.ca/bids?bidid=10806"},
    {"id":2,"title":"26-03-VWRC Fermenter Door Install","type":"Request for Quotations","org":"Vernon","url":"https://www.civicinfo.bc.ca/bids?bidid=10809"},
    {"id":3,"title":"RFP 2026-04 Supply of Self Contained Breathing Apparatus (SCBA)","type":"Request for Proposals","org":"Agassiz","url":"https://www.civicinfo.bc.ca/bids?bidid=10808"},
    {"id":4,"title":"Esquimalt Recreation Centre Roofing Project","type":"Invitation to Bid","org":"Esquimalt","url":"https://www.civicinfo.bc.ca/bids?bidid=10807"},
    {"id":6,"title":"RFP No. 26-001 Supply and Deliver Auto Extrication Equipment","type":"Request for Proposals","org":"Coquitlam","url":"https://www.civicinfo.bc.ca/bids?bidid=10805"}
]

results = []
for t in tenders:
    # Logic for Evaluation
    is_relevant = True
    requires_login = False
    platform = "Unknown"
    reason = ""

    # Check for login-heavy platforms
    if "civicinfo.bc.ca" in t["url"]:
        requires_login = True
        platform = "CivicInfo BC"

    # Evaluate Relevance for Industrial/The Project Wheel
    title_lower = t["title"].lower()
    
    # Noise detection (Equipment vs Projects)
    if "supply" in title_lower and "equipment" in title_lower:
        is_relevant = False
        reason = "Equipment supply only (Non-project)"
    elif "breathing apparatus" in title_lower:
        is_relevant = False
        reason = "PPE/Goods supply (Non-industrial construction)"
    elif "condenser replacement" in title_lower:
        is_relevant = True
        reason = "Mechanical Industrial/Commercial Project"
    elif "fermenter door" in title_lower:
        is_relevant = True
        reason = "Specialized Industrial Installation"
    elif "roofing project" in title_lower:
        is_relevant = True
        reason = "Facility Infrastructure"

    results.append({
        "id": t["id"],
        "title": t["title"],
        "is_relevant": is_relevant,
        "requires_login": requires_login,
        "platform": platform,
        "reason": reason
    })

print(json.dumps(results, indent=2))
