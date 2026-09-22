"""Read-only smoke checks. Credentials are read from environment, never arguments/logs.
Usage: python scripts/smoke_deployment.py https://deployment.example [expected_revision]
Optional SMOKE_USERNAME/SMOKE_PASSWORD enable authenticated read checks.
"""
import json
import os
import sys
import time
import requests

base = sys.argv[1].rstrip('/')
expected = sys.argv[2] if len(sys.argv) > 2 else None
session = requests.Session()
checks = []

def check(path, status, method='GET', **kwargs):
    started = time.perf_counter()
    response = session.request(method, base + path, timeout=30, **kwargs)
    checks.append({'path': path, 'status': response.status_code,
                   'seconds': round(time.perf_counter() - started, 3), 'passed': response.status_code == status})
    response.raise_for_status() if status < 400 else None
    if response.status_code != status:
        raise RuntimeError(f'Unexpected status for {path}: {response.status_code}')
    return response

try:
    check('/', 200)
    health = check('/health', 200).json()
    if expected and health.get('revision') != expected:
        raise RuntimeError('Deployed revision does not match expected commit')
    check('/api/claims?limit=1&compact=true', 401)
    state = check('/api/session', 200)
    assert state.json().get('authenticated') is False
    for name in ['X-Content-Type-Options', 'X-Frame-Options', 'Cache-Control']:
        assert state.headers.get(name), f'Missing {name}'
    username, password = os.getenv('SMOKE_USERNAME'), os.getenv('SMOKE_PASSWORD')
    if username and password:
        check('/api/login', 200, 'POST', json={'username': username, 'password': password})
        rows = check('/api/claims?limit=1&compact=true', 200).json()
        assert len(rows['items']) <= 1
        assert rows['evidence_legend']
        check('/api/exports/claims?limit=1', 200)
    else:
        checks.append({'check': 'authenticated production reads', 'passed': False, 'blocked': 'SMOKE_USERNAME/SMOKE_PASSWORD not configured'})
except Exception as error:
    checks.append({'error': type(error).__name__, 'message': str(error).split('?')[0], 'passed': False})
print(json.dumps({'base_url': base, 'checks': checks}, indent=2))
sys.exit(0 if all(item['passed'] for item in checks) else 1)
