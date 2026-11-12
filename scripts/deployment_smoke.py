"""Explicit deployment checks; no message or model call without an opt-in flag.

python scripts/deployment_smoke.py --url https://YOUR_FRONTEND --sse
python scripts/deployment_smoke.py --url https://YOUR_FRONTEND --live-model
"""
import argparse
import json
import time
import uuid
import httpx


def check(url, sse=False, live_model=False):
    results = []
    with httpx.Client(base_url=url.rstrip('/'), timeout=70, follow_redirects=False) as client:
        for route in ('/', '/app', '/legacy', '/docs', '/api/health/live', '/api/health/ready', '/api/catalog'):
            response = client.get(route)
            assert response.status_code == 200, (route, response.status_code)
            if route.startswith('/api/'):
                assert 'no-store' in response.headers.get('cache-control', '')
            results.append({'route': route, 'status': response.status_code})
        response = client.get('/chat')
        assert response.status_code == 307 and response.headers['location'] == '/app'
        results.append({'route': '/chat', 'status': 307})
        provider = client.get('/api/inference/status').json()
        results.append({'provider_status': provider})
        if live_model:
            # Synthetic, non-identifying profile only. Never pass model keys to this client.
            response = client.post('/api/v2/conversation', json={'text': 'I am 62 and live in Maharashtra.',
                                                               'lang': 'en', 'request_id': uuid.uuid4().hex})
            assert response.status_code == 200, ('Hosted inference did not succeed', response.status_code)
            sid = response.json()['session_id']
            assert client.get('/api/inference/status').json()['state'] == 'working', 'Mock or unverified provider'
            assert client.delete('/api/v2/conversation/' + sid).status_code == 200
            results.append({'hosted_inference': 'genuine request succeeded'})
        if sse:
            response = client.post('/api/sessions', json={'profile': {'age': 62, 'state': 'maharashtra'}, 'web': False})
            assert response.status_code == 200, response.status_code
            sid = response.json()['id']
            sequence = None
            started = time.monotonic()
            with client.stream('GET', f'/api/sessions/{sid}/events') as stream:
                assert stream.status_code == 200
                assert stream.headers['content-type'].startswith('text/event-stream')
                assert 'no-store' in stream.headers.get('cache-control', '')
                for line in stream.iter_lines():
                    if line.startswith('id: '):
                        sequence = int(line[4:])
                    if line.startswith('data:'):
                        assert time.monotonic() - started < 10, 'Proxy may be buffering SSE'
                        break
            assert sequence is not None
            # A bounded poll proves the same resume cursor used by native EventSource.
            response = client.get(f'/api/sessions/{sid}/events?once=true', headers={'Last-Event-ID': str(sequence)})
            assert response.status_code == 200
            assert all(event['seq'] > sequence for event in response.json())
            for _ in range(30):
                snapshot = client.get(f'/api/sessions/{sid}').json()
                if snapshot['state'] not in ('starting', 'investigating'):
                    break
                time.sleep(0.2)
            client.delete(f'/api/sessions/{sid}')
            results.append({'sse': 'incremental data and resume cursor verified'})
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--sse', action='store_true', help='Create a synthetic legacy session and test streaming (may invoke configured planner)')
    parser.add_argument('--live-model', action='store_true', help='Make one genuine configured hosted-model request')
    args = parser.parse_args()
    check(args.url, args.sse, args.live_model)
