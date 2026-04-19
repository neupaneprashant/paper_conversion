import json
import urllib.request
import sys

# Test 1: Quoted path
payload = {"input_path": '"samples/acm_sample"', "source_format": "acm", "target_format": "ieee"}
req = urllib.request.Request('http://127.0.0.1:8090/api/jobs', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'}, method='POST')
try:
    with urllib.request.urlopen(req, timeout=5) as r:
        result = json.loads(r.read())
        print("✓ Test 1 (Quoted path):", result.get("job_id", "NO JOB ID"))
        print("  Status:", result.get("status"))
except Exception as e:
    print(f"✗ Test 1 Error: {e}")

# Test 2: Unquoted valid path
print("\n---")
payload2 = {"input_path": "samples/acm_sample", "source_format": "acm", "target_format": "ieee"}
req2 = urllib.request.Request('http://127.0.0.1:8090/api/jobs', data=json.dumps(payload2).encode(), headers={'Content-Type': 'application/json'}, method='POST')
try:
    with urllib.request.urlopen(req2, timeout=5) as r:
        result = json.loads(r.read())
        print("✓ Test 2 (Unquoted valid):", result.get("job_id", "NO JOB ID"))
        print("  Status:", result.get("status"))
except Exception as e:
    print(f"✗ Test 2 Error: {e}")

# Test 3: Invalid path
print("\n---")
payload3 = {"input_path": "/fake/path", "source_format": "acm", "target_format": "ieee"}
req3 = urllib.request.Request('http://127.0.0.1:8090/api/jobs', data=json.dumps(payload3).encode(), headers={'Content-Type': 'application/json'}, method='POST')
try:
    with urllib.request.urlopen(req3, timeout=5) as r:
        result = json.loads(r.read())
        print("✗ Test 3 (Bad path): Should have failed!")
except urllib.error.HTTPError as e:
    error_body = json.loads(e.read())
    print(f"✓ Test 3 (Expected 404): {error_body.get('error')}")
