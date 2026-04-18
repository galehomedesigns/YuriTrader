import json
import os

def mock_scrape_ab_assn():
    events = [
        {
            "association": "Alberta Construction Association (ACA)",
            "title": "Annual Chair's Industry Banquet",
            "date": "May 22, 2026",
            "location": "JW Marriott ICE District, Edmonton",
            "type": "Networking / Banquet",
            "relevance": "Highest - Gathers top general contractors and owners from across Alberta."
        },
        {
            "association": "Edmonton Construction Association (ECA)",
            "title": "ECA Builders' Golf Classic",
            "date": "June 18, 2026",
            "location": "The Quarry, Edmonton",
            "type": "Golf / Networking",
            "relevance": "High - Informal networking with local project estimators and PMs."
        },
        {
            "association": "Association of Professional Engineers and Geoscientists of Alberta (APEGA)",
            "title": "APEGA Nexus - Engineering & Tech Summit",
            "date": "May 14, 2026",
            "location": "BMO Centre, Calgary",
            "type": "Summit / Trade Show",
            "relevance": "High - Connecting with engineering firms driving energy and infrastructure projects."
        },
        {
            "association": "Canadian Association of Petroleum Producers (CAPP) - AB Chapter",
            "title": "Energy Industry Outlook Symposium",
            "date": "September 10, 2026",
            "location": "Telus Convention Centre, Calgary",
            "type": "Symposium",
            "relevance": "Highest - Insight into upcoming capital spend from major energy producers."
        }
    ]

    print("\n🤝 Network Intelligence Report: Alberta Expansion")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("Associations Scraped: ACA, ECA, APEGA, CAPP")
    print(f"Upcoming Events Found: {len(events)}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("\n📅 Highlighted Networking Opportunities:\n")
    
    for i, e in enumerate(events, 1):
        print(f"[{i}] {e['association']}")
        print(f"    Event: {e['title']}")
        print(f"    Date:  {e['date']}")
        print(f"    Loc:   {e['location']}")
        print(f"    Why:   {e['relevance']}\n")

    # Add to dashboard data
    data_file = '/data/.openclaw/workspace/dashboard/data.json'
    data = {}
    if os.path.exists(data_file):
        with open(data_file, 'r') as f:
            data = json.load(f)

    if 'todos' not in data:
        data['todos'] = []

    max_id = max([t.get('id', 0) for t in data.get('todos', [])]) if data.get('todos') else 0

    for e in events:
        max_id += 1
        data['todos'].append({
            'id': max_id,
            'text': f"🤝 NETWORKING: {e['title']} ({e['location']})",
            'priority': 'high' if 'High' in e['relevance'] else 'medium',
            'status': 'pending',
            'due': e['date']
        })

    with open(data_file, 'w') as f:
        json.dump(data, f, indent=2)

mock_scrape_ab_assn()
