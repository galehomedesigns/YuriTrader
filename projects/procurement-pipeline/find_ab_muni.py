import json

def fetch_ab_municipalities():
    ab_targets = [
        {"name": "Alberta Purchasing Connection (APC)", "region": "Province-wide", "portal_type": "apc", "priority": "high"},
        {"name": "City of Calgary", "region": "Calgary Region", "portal_type": "sap/ariba", "priority": "high"},
        {"name": "City of Edmonton", "region": "Edmonton Region", "portal_type": "sap/ariba", "priority": "high"},
        {"name": "Regional Municipality of Wood Buffalo", "region": "Northern Alberta", "portal_type": "bidsandtenders", "priority": "high"},
        {"name": "Strathcona County", "region": "Edmonton Region", "portal_type": "bidsandtenders", "priority": "high"},
        {"name": "City of Red Deer", "region": "Central Alberta", "portal_type": "bidsandtenders", "priority": "high"},
        {"name": "City of Lethbridge", "region": "Central Alberta", "portal_type": "bidsandtenders", "priority": "high"},
        {"name": "City of Medicine Hat", "region": "Southern Alberta", "portal_type": "bidsandtenders", "priority": "medium"},
        {"name": "City of Grande Prairie", "region": "Northern Alberta", "portal_type": "bidsandtenders", "priority": "high"},
        {"name": "City of Airdrie", "region": "Calgary Region", "portal_type": "bidsandtenders", "priority": "medium"},
        {"name": "City of St. Albert", "region": "Edmonton Region", "portal_type": "bidsandtenders", "priority": "high"},
        {"name": "Rocky View County", "region": "Calgary Region", "portal_type": "bidsandtenders", "priority": "medium"},
        {"name": "City of Spruce Grove", "region": "Edmonton Region", "portal_type": "bidsandtenders", "priority": "medium"},
        {"name": "City of Lloydminster", "region": "Southern Alberta", "portal_type": "bidsandtenders", "priority": "medium"},
        {"name": "Parkland County", "region": "Edmonton Region", "portal_type": "bidsandtenders", "priority": "medium"}
    ]
    
    with open('/data/.openclaw/workspace/projects/procurement-pipeline/ab_municipalities.json', 'w') as f:
        json.dump(ab_targets, f, indent=2)
        
    print(f"Saved {len(ab_targets)} major Alberta municipal targets to ab_municipalities.json")

fetch_ab_municipalities()
