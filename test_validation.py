import json
import urllib.request
import urllib.error

# Test 1: Try to submit a file path (should fail)
print("Test 1: Submitting a file path (should fail with 400)")
payload1 = {
    "input_path": "samples/acm_sample/main.tex",
    "source_format": "acm",
    "target_format": "ieee"
}
req1 = urllib.request.Request(
    'http://127.0.0.1:8090/api/jobs',
    data=json.dumps(payload1).encode(),
    headers={'Content-Type': 'application/json'},
    method='POST'
)
try:
    with urllib.request.urlopen(req1, timeout=5) as r:
        print(f"✗ Unexpected success: {r.status}")
except urllib.error.HTTPError as e:
    error = json.loads(e.read())
    print(f"✓ Got expected error ({e.code}): {error.get('error')}")
except Exception as e:
    print(f"✗ Unexpected error: {e}")

# Test 2: Submit a valid folder path (should succeed)
print("\nTest 2: Submitting a folder path (should succeed)")
payload2 = {
    "input_path": "samples/acm_sample",
    "source_format": "acm",
    "target_format": "ieee"
}
req2 = urllib.request.Request(
    'http://127.0.0.1:8090/api/jobs',
    data=json.dumps(payload2).encode(),
    headers={'Content-Type': 'application/json'},
    method='POST'
)
try:
    with urllib.request.urlopen(req2, timeout=5) as r:
        result = json.loads(r.read())
        print(f"✓ Success: Job created with ID {result.get('job_id')}")
except Exception as e:
    print(f"✗ Failed: {e}")
