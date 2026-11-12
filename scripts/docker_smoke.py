"""Owner-run disposable Docker/PostgreSQL build, boot and restart validation.

Never runs during ordinary tests. Requires --project-name yojana-v24-smoke-...
and an ignored staging env file. It creates/starts that isolated Compose project,
does not send model/WhatsApp requests, and leaves containers/volumes for inspection.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import os
import socket
import time
import uuid
import httpx

ROOT = Path(__file__).resolve().parents[1]


def check(env_file, project):
    if not shutil.which('docker'):
        raise SystemExit('Docker is unavailable: image build and PostgreSQL restart checks were not run.')
    if not project.startswith('yojana-v24-smoke-'):
        raise SystemExit('Use a unique disposable project name starting yojana-v24-smoke-.')
    environment = dict(os.environ)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    environment['BACKEND_BIND_PORT'] = str(port)
    command = ['docker', 'compose', '--project-name', project, '--env-file', str(Path(env_file).resolve()),
               '--file', str(ROOT / 'compose.yaml')]
    def run(*args, **kwargs):
        return subprocess.run(command + list(args), cwd=ROOT, env=environment, check=True, **kwargs)
    run('config', '--quiet')
    run('up', '-d', '--build')
    url = f'http://127.0.0.1:{port}'
    def ready():
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                if httpx.get(url + '/readyz', timeout=3).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
        raise RuntimeError('Container readiness timed out. Inspect the isolated Compose project logs.')
    ready()
    run('exec', '-T', 'backend', 'python', '-m', 'web.migrate')
    run('exec', '-T', 'backend', 'python', '-m', 'web.migrate')
    sid = 'docker-smoke-' + uuid.uuid4().hex
    # A synthetic test capability, never a model, WAHA or database credential.
    token = 'fixture-docker-smoke-owner'
    code = "\n".join([
        'from web.case_store import transaction',
        f'with transaction({sid!r}) as tx:',
        f"    tx.save({sid!r}, {{'profile': {{'age': 62}}, 'pending': {{'field': 'occupation'}}}}, token={token!r})",
        f"    tx.message({sid!r}, 'user', 'Synthetic deployment persistence check')"])
    run('exec', '-T', 'backend', 'python', '-c', code)
    with httpx.Client(base_url=url, headers={'Authorization': 'Bearer ' + token}, timeout=10) as client:
        def saved():
            response = client.get('/api/cases/' + sid)
            assert response.status_code == 200, response.status_code
            assert response.json()['profile']['age'] == 62
            assert response.json()['pending_question']['field'] == 'occupation'
        saved()
        run('restart', 'backend')
        ready()
        saved()
        run('restart', 'postgres')
        ready()
        saved()
        assert client.delete('/api/v2/conversation/' + sid).status_code == 200
    print('Docker image build/boot, migrations, backend restart and PostgreSQL restart persistence passed.')
    print(f'Isolated Compose project {project} remains available on loopback port {port}; inspect it, then stop it manually without deleting unrelated volumes.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', required=True)
    parser.add_argument('--project-name', required=True)
    args = parser.parse_args()
    check(args.env_file, args.project_name)
