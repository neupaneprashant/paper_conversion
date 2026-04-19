import json
import urllib.request

# Test the browse API
try:
    req = urllib.request.Request('http://127.0.0.1:8090/api/browse?path=samples')
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read())
        print(f"✓ Browse API works!")
        print(f"  Current path: {data.get('current_path')}")
        print(f"  Items: {len(data.get('items', []))}")
        print(f"  First item: {data.get('items', [{}])[0].get('name')}")
except Exception as e:
    print(f"✗ Browse API error: {e}")
