import json
import urllib.request

jobs = ["job-1775972811-0b1e2c3e", "job-1775972811-b085041d"]

for job_id in jobs:
    req = urllib.request.Request(f'http://127.0.0.1:8090/api/jobs/{job_id}')
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
            result = data.get('result', {})
            validation = result.get('validation', {})
            
            print(f"Job: {job_id}")
            print(f"  Overall Status: {data.get('status')}")
            print(f"  Conversion Direction: {result.get('direction')}")
            print(f"  Template Compliance: {validation.get('template_compliance')}")
            print(f"  Citation Compliance: {validation.get('citation_compliance')}")
            print(f"  Compile Status: {validation.get('compile_status')}")
            print(f"  Warnings: {validation.get('warnings', [])}")
            print()
    except Exception as e:
        print(f"Error: {e}")
