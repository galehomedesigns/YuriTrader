import json
import urllib.request

def fetch_bc_municipalities():
    # CivicInfo BC already aggregates almost all 162 BC municipalities and 27 regional districts.
    # Our pipeline currently hits CivicInfo BC as a single source which provides excellent coverage.
    # However, to be exhaustive, we should list the major BC regional authorities and cities 
    # that might use separate portals (like BidsAndTenders or Bonfire) exclusively, bypassing CivicInfo.
    
    bc_targets = [
        {"name": "Metro Vancouver Regional District", "region": "Metro Vancouver", "portal_type": "bidsandtenders", "priority": "high"},
        {"name": "City of Vancouver", "region": "Metro Vancouver", "portal_type": "sciquest/sap", "priority": "high"},
        {"name": "City of Surrey", "region": "Metro Vancouver", "portal_type": "bidsandtenders", "priority": "high"},
        {"name": "City of Burnaby", "region": "Metro Vancouver", "portal_type": "bidsandtenders", "priority": "high"},
        {"name": "City of Richmond", "region": "Metro Vancouver", "portal_type": "bidsandtenders", "priority": "high"},
        {"name": "Capital Regional District (CRD)", "region": "Vancouver Island", "portal_type": "bonfire", "priority": "high"},
        {"name": "City of Victoria", "region": "Vancouver Island", "portal_type": "bonfire", "priority": "high"},
        {"name": "City of Nanaimo", "region": "Vancouver Island", "portal_type": "bidsandtenders", "priority": "high"},
        {"name": "City of Kelowna", "region": "Okanagan", "portal_type": "bonfire", "priority": "high"},
        {"name": "City of Kamloops", "region": "Interior", "portal_type": "bidsandtenders", "priority": "high"},
        {"name": "City of Prince George", "region": "Northern BC", "portal_type": "bidsandtenders", "priority": "high"},
        {"name": "District of Squamish", "region": "Metro Vancouver", "portal_type": "bidsandtenders", "priority": "medium"},
        {"name": "City of Coquitlam", "region": "Metro Vancouver", "portal_type": "bidsandtenders", "priority": "medium"},
        {"name": "City of Abbotsford", "region": "Fraser Valley", "portal_type": "bidsandtenders", "priority": "high"},
        {"name": "City of Chilliwack", "region": "Fraser Valley", "portal_type": "bidsandtenders", "priority": "medium"}
    ]
    
    with open('/home/tonygale/openclaw/projects/procurement-pipeline/bc_municipalities.json', 'w') as f:
        json.dump(bc_targets, f, indent=2)
        
    print(f"Saved {len(bc_targets)} major BC municipal targets to bc_municipalities.json")

fetch_bc_municipalities()
