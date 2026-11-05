"""Read-only smoke checks against an already running Compose project."""
import json
from pathlib import Path
import subprocess
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ('gateway', 'harness', 'sandbox-manager', 'llm-proxy')


def docker(*args):
    return subprocess.check_output(['docker', *args], cwd=ROOT, text=True, timeout=30)


def main():
    config = json.loads(docker('compose', 'config', '--format', 'json'))
    health_code = "import json,urllib.request; print(json.dumps(json.load(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4))))"
    for name in SERVICES:
        service = config['services'][name]
        assert bool(service.get('ports')) == (name == 'gateway'), f'Unexpected published ports: {name}'
        container_id = docker('compose', 'ps', '-q', name).strip()
        assert container_id, f'{name}: not running; run make up first'
        info = json.loads(docker('inspect', container_id))[0]
        assert info['State']['Health']['Status'] == 'healthy', f'{name}: not healthy'
        expected_user = '0:0' if name == 'sandbox-manager' else '10001:10001'
        assert info['Config']['User'] == expected_user, f'{name}: unexpected user'
        socket_mounts = [m for m in info['Mounts'] if m['Destination'] == '/var/run/docker.sock']
        assert bool(socket_mounts) == (name == 'sandbox-manager'), f'{name}: unexpected Docker socket'
        env_names = {entry.split('=', 1)[0] for entry in info['Config']['Env']}
        assert ('LLM_API_KEY' in env_names) == (name == 'llm-proxy'), f'{name}: unexpected secret configuration'
        if name == 'gateway':
            bindings = info['NetworkSettings']['Ports'].get('8000/tcp')
            assert bindings, 'Gateway port is not published'
            url = 'http://127.0.0.1:' + bindings[0]['HostPort'] + '/health'
            with build_opener(ProxyHandler({})).open(url, timeout=5) as response:
                assert response.status == 200, 'Gateway is unreachable from Docker host'
        # JSON only; never print docker inspect (it contains environment values).
        state = json.loads(docker('compose', 'exec', '-T', name, 'python', '-c', health_code))
        assert state['service'] == name and state['status'] == 'ready', f'{name}: bad readiness response'
        print(f'{name}: healthy, expected UID, environment/socket scope OK')
    print('Server smoke checks passed. Run make test-sandbox for real command execution.')


if __name__ == '__main__':
    main()
