"""Run in the harness container: exercise the sandbox protocol without an LLM."""
import base64
import json
import os
from pathlib import Path
import time
from urllib.request import Request, urlopen
from uuid import uuid4

URL = os.environ.get('SANDBOX_URL', 'http://sandbox-manager:8000')
TOKEN = Path(os.environ.get('SANDBOX_TOKEN_FILE', '/run/runtime-auth/sandbox.token')).read_text().strip()
epoch = None


def call(kind, **fields):
    data = {'version': 'v0', 'type': kind, 'request_id': uuid4().hex,
            'expires_at': int(time.time())+180, 'timeout_s': 20, **fields}
    if epoch is not None:
        data['server_epoch'] = epoch
    request = Request(URL+'/v0/requests', data=json.dumps(data).encode(),
                      headers={'Content-Type': 'application/json', 'Authorization': 'Bearer '+TOKEN})
    with urlopen(request, timeout=25) as response:
        return json.load(response)


def execute(box, command):
    op = uuid4().hex
    call('exec', sandbox_id=box, operation_id=op, command=command)
    end = time.monotonic()+25
    while time.monotonic() < end:
        state = call('operation.get', sandbox_id=box, operation_id=op)
        if state['state'] == 'terminal':
            if state['result']['status'] != 'success':
                raise RuntimeError('Sandbox command failed: '+json.dumps(state['result']))
            return state['result']
        time.sleep(.05)
    raise TimeoutError('Operation did not finish')


def main():
    global epoch
    epoch = call('connect')['server_epoch']
    box = call('provision', session_id=uuid4().hex,
               workdir={'kind': 'fixture', 'source_id': 'B1_1', 'mode': 'rw'})['sandbox_id']
    try:
        # Deliberately scripted: this demonstrates execution, not agent quality.
        code = "from pathlib import Path; import re; text=Path('config/app.yaml').read_text(); port=re.search(r'^\\s*port:\\s*(\\d+)',text,re.M)[1]; Path('answer.txt').write_text(port)"
        execute(box, {'name': 'RunCommand', 'argv': ['python', '-c', code], 'cwd': '.'})
        result = execute(box, {'name': 'ReadFile', 'path': 'answer.txt'})
        answer = base64.b64decode(result['content_base64']).decode()
        assert answer == '8137', 'Unexpected demo answer'
        artifact = call('collect', sandbox_id=box)
        print(json.dumps({'sandbox_id': box, 'answer.txt': answer,
                          'artifact_bytes': artifact['size_bytes'], 'status': 'success',
                          'note': 'Scripted sandbox demo; no LLM used'}, ensure_ascii=False, indent=2))
    finally:
        call('release', sandbox_id=box)
        print('Sandbox released')


if __name__ == '__main__':
    main()
