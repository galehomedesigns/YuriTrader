import os
import json
import urllib.request

url = os.environ.get("SUPABASE_URL") + "/rest/v1/tenders?limit=1"
headers = {
    "apikey": os.environ.get("SUPABASE_SERVICE_KEY"),
    "Authorization": "Bearer " + os.environ.get("SUPABASE_SERVICE_KEY"),
}
req = urllib.request.Request(url, headers=headers)
try:
    with urllib.request.urlopen(req) as resp:
        print(json.loads(resp.read().decode()))
except Exception as e:
    print(e)
