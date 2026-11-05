"""Run inside sandbox-manager: stdlib HTTP client tests against its real Docker sandboxes."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from jsonschema import Draft202012Validator
from runtime.docker_api import Engine, EngineError

BASE = 'http://127.0.0.1:8000'
TOKEN = Path('/run/runtime-auth/sandbox.token').read_text().strip()
SCHEMA = json.loads(Path('/app/contracts/harness-sandbox.v0.schema.json').read_text())
VALIDATOR = Draft202012Validator(SCHEMA)


def http(path, data=None, token=TOKEN):
    headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
    try:
        return urlopen(Request(BASE+path, data=json.dumps(data).encode() if data is not None else None,
                               headers=headers), timeout=40)
    except HTTPError as error:
        return error


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.epoch = None
        self.boxes = []
        self.engine = Engine()
        status, body = self.send('connect')
        self.assertEqual(status, 200)
        self.epoch = body['server_epoch']

    def tearDown(self):
        for box in self.boxes:
            status, _ = self.send('release', sandbox_id=box)
            self.assertEqual(status, 200)

    def request(self, kind, **fields):
        value = {'version': 'v0', 'type': kind, 'request_id': uuid4().hex,
                 'expires_at': int(time.time())+300, 'timeout_s': 20, **fields}
        if self.epoch:
            value['server_epoch'] = self.epoch
        return value

    def raw(self, request, token=TOKEN):
        with http('/v0/requests', request, token) as response:
            body = json.load(response)
            VALIDATOR.validate(body)
            return response.status, body

    def send(self, kind, **fields):
        return self.raw(self.request(kind, **fields))

    def provision(self, mode='rw', source='B1_1'):
        status, body = self.send('provision', session_id=uuid4().hex,
                                workdir={'kind': 'fixture', 'source_id': source, 'mode': mode})
        self.assertEqual(status, 200, body)
        box = body['sandbox_id']
        self.boxes.append(box)
        return box

    def start(self, box, command, **fields):
        req = self.request('exec', sandbox_id=box, operation_id=uuid4().hex, command=command, **fields)
        status, body = self.raw(req)
        self.assertEqual(status, 202, body)
        return req

    def wait(self, req, seconds=30):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            status, body = self.send('operation.get', sandbox_id=req['sandbox_id'], operation_id=req['operation_id'])
            self.assertEqual(status, 200)
            if body['state'] == 'terminal':
                return body['result']
            time.sleep(.05)
        self.fail('Operation did not finish')

    def run_command(self, box, argv, **kwargs):
        return self.wait(self.start(box, {'name': 'RunCommand', 'argv': argv, 'cwd': '.'}, **kwargs))

    def python(self, box, code, **kwargs):
        return self.run_command(box, ['python', '-c', code], **kwargs)

    def container(self, box):
        import os
        project = os.environ['SANDBOX_PROJECT']
        matches = [x for x in self.engine.containers(project) if x['Labels']['agent_harness.sandbox'] == box]
        self.assertEqual(len(matches), 1)
        return matches[0]['Id']

    def test_read_write_binary_and_nonzero_exit(self):
        box = self.provision()
        read = self.wait(self.start(box, {'name': 'ReadFile', 'path': 'config/app.yaml'}))
        self.assertIn(b'8137', base64.b64decode(read['content_base64']))
        data = 'Товар;990\r\n'.encode('cp1251')
        encoded = base64.b64encode(data).decode()
        write = self.wait(self.start(box, {'name': 'WriteFile', 'path': 'prices.csv', 'content_base64': encoded}))
        self.assertEqual(write['status'], 'success')
        read = self.wait(self.start(box, {'name': 'ReadFile', 'path': 'prices.csv'}))
        self.assertEqual(base64.b64decode(read['content_base64']), data)
        failure = self.run_command(box, ['sh', '-c', 'echo OK; echo reason >&2; exit 1'])
        self.assertEqual(failure['exit_code'], 1)
        self.assertEqual(failure['error']['class'], 'command_error')
        self.assertEqual(failure['stdout'], 'OK\n')
        self.assertEqual(failure['stderr'], 'reason\n')

    def test_request_and_operation_deduplication(self):
        box = self.provision()
        req = self.start(box, {'name': 'RunCommand', 'argv': ['sh', '-c', 'echo once >> count'], 'cwd': '.'})
        self.assertEqual(self.wait(req)['status'], 'success')
        self.assertEqual(self.raw(req)[0], 202)
        duplicate = {**req, 'request_id': uuid4().hex}
        self.assertEqual(self.raw(duplicate)[0], 202)
        self.assertEqual(self.raw({**req, 'timeout_s': 5})[0], 409)
        self.assertEqual(self.raw({**duplicate, 'request_id': uuid4().hex, 'timeout_s': 5})[0], 409)
        read = self.run_command(box, ['cat', 'count'])
        self.assertEqual(read['stdout'], 'once\n')

    def test_events_reconnect_and_one_final_result(self):
        box = self.provision()
        req = self.start(box, {'name': 'RunCommand', 'argv': ['sh', '-c', 'echo first; sleep 0.2; echo last'], 'cwd': '.'})
        self.wait(req)
        def events(after):
            subscription = self.request('events.subscribe', sandbox_id=box, after_event_id=after, timeout_s=1)
            with http('/v0/events', subscription) as stream:
                self.assertEqual(stream.status, 200)
                values = [json.loads(line[6:]) for line in stream.read().decode().splitlines() if line.startswith('data: ')]
            for value in values:
                VALIDATOR.validate(value)
            return values
        first = events(0)
        self.assertEqual(sum(e['type']=='exec.result' for e in first), 1)
        self.assertTrue(any(e['type']=='exec.output' for e in first))
        self.assertEqual(events(first[0]['event_id']), first[1:])

    def test_cancel_kills_entire_container_and_repeated_cancel_is_safe(self):
        box = self.provision()
        container = self.container(box)
        req = self.start(box, {'name': 'RunCommand', 'argv': ['python', '-c',
            'import subprocess,time; subprocess.Popen(["sleep","60"],start_new_session=True); time.sleep(60)'], 'cwd': '.'})
        time.sleep(.4)
        self.assertEqual(self.send('cancel', sandbox_id=box, operation_id=req['operation_id'])[0], 202)
        value = self.wait(req)
        self.assertEqual(value['status'], 'cancelled')
        state = self.engine.request('GET', f'/containers/{container}/json')
        self.assertFalse(state['State']['Running'])
        self.assertEqual(state['State']['Pid'], 0)
        status, body = self.send('cancel', sandbox_id=box, operation_id=req['operation_id'])
        self.assertEqual(body['status'], 'already_terminal')
        self.assertEqual(self.send('collect', sandbox_id=box)[0], 409)

    def test_timeout_is_terminal_once(self):
        box = self.provision()
        container = self.container(box)
        begin = time.monotonic()
        value = self.run_command(box, ['sleep', '30'], timeout_s=1)
        self.assertEqual(value['status'], 'timed_out')
        self.assertEqual(value['error']['class'], 'timeout')
        self.assertLess(time.monotonic()-begin, 8)
        self.assertFalse(self.engine.request('GET', f'/containers/{container}/json')['State']['Running'])

    def test_limits_secrets_and_network_configuration(self):
        box = self.provision()
        state = self.engine.request('GET', f'/containers/{self.container(box)}/json')
        host = state['HostConfig']
        self.assertEqual(state['Config']['User'], '10001:10001')
        self.assertEqual(host['NetworkMode'], 'none')
        self.assertTrue(host['ReadonlyRootfs'])
        self.assertIn('ALL', host['CapDrop'])
        self.assertIn('no-new-privileges:true', host['SecurityOpt'])
        self.assertGreater(host['Memory'], 0)
        self.assertEqual(host['MemorySwap'], host['Memory'])
        self.assertGreater(host['NanoCpus'], 0)
        self.assertGreater(host['PidsLimit'], 0)
        code = '''import os,json,socket
from pathlib import Path
assert os.getuid()==10001
assert 'LLM_API_KEY' not in os.environ
assert not Path('/var/run/docker.sock').exists()
assert not Path('/run/runtime-auth/sandbox.token').exists()
assert not Path('/app').exists()
assert 'NoNewPrivs:\\t1' in Path('/proc/self/status').read_text()
assert int(Path('/sys/fs/cgroup/memory.max').read_text())>0
assert int(Path('/sys/fs/cgroup/pids.max').read_text())>0
assert Path('/sys/fs/cgroup/cpu.max').read_text().split()[0]!='max'
try:
 socket.create_connection(('1.1.1.1',80),timeout=.2)
except OSError:
 print('network denied')
else:
 raise AssertionError('Unexpected network')
print(json.dumps(dict(os.environ)))
'''
        value = self.python(box, code)
        self.assertEqual(value['status'], 'success', value)
        self.assertNotIn(TOKEN, value['stdout'])
        self.assertIn('network denied', value['stdout'])
        write_root = self.python(box, "open('/rootfs-write-test','w').write('x')")
        self.assertEqual(write_root['status'], 'failed')

    def test_disk_quota_enforced(self):
        box = self.provision(source='empty')
        value = self.python(box, '''import errno,os
try:
 with open('/work/fill','wb',buffering=0) as f:
  for _ in range(4096):
   f.write(b'x'*1024*1024)
except OSError as e:
 assert e.errno==errno.ENOSPC,e
 print('ENOSPC')
finally:
 os.unlink('/work/fill')
''')
        self.assertEqual(value['status'], 'success', value)
        self.assertEqual(value['stdout'], 'ENOSPC\n')

    def test_read_only_applies_to_commands_too(self):
        box = self.provision(mode='ro')
        self.assertEqual(self.run_command(box, ['cat', 'config/app.yaml'])['status'], 'success')
        failure = self.run_command(box, ['sh', '-c', 'echo bad > config/app.yaml'])
        self.assertEqual(failure['status'], 'failed')
        failure = self.run_command(box, ['sh', '-c', 'chmod u+w config/app.yaml'])
        self.assertEqual(failure['status'], 'failed')
        status, _ = self.send('exec', sandbox_id=box, operation_id=uuid4().hex,
                             command={'name': 'WriteFile', 'path': 'x', 'content_base64': ''})
        self.assertEqual(status, 400)

    def test_paths_symlinks_and_special_files(self):
        box = self.provision()
        self.assertEqual(self.run_command(box, ['sh','-c','ln -s /etc/passwd outside; ln -s /etc dir; mkfifo fifo'])['status'], 'success')
        for path in ('outside', 'dir/passwd', 'fifo'):
            value = self.wait(self.start(box, {'name': 'ReadFile', 'path': path}))
            self.assertEqual(value['status'], 'failed')
        status, _ = self.send('exec', sandbox_id=box, operation_id=uuid4().hex,
                             command={'name':'ReadFile','path':'../etc/passwd'})
        self.assertEqual(status, 400)
        self.assertEqual(self.send('collect', sandbox_id=box)[0], 400)

    def test_collect_and_release(self):
        box = self.provision()
        container = self.container(box)
        self.run_command(box, ['sh', '-c', 'echo 8137 > answer.txt'])
        req = self.request('collect', sandbox_id=box)
        status, reply = self.raw(req)
        self.assertEqual(status, 200, reply)
        self.assertEqual(self.raw(req)[1], reply)
        with http('/v0/artifacts/'+reply['artifact_id']) as response:
            raw = response.read()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), reply['sha256'])
        self.assertEqual(len(raw), reply['size_bytes'])
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            self.assertEqual(archive.extractfile('answer.txt').read(), b'8137\n')
            self.assertNotIn('check.py', archive.getnames())
        self.assertEqual(self.send('release', sandbox_id=box)[0], 200)
        self.assertEqual(self.send('release', sandbox_id=box)[0], 200)
        with self.assertRaises(EngineError):
            self.engine.request('GET', f'/containers/{container}/json')
        with http('/v0/artifacts/'+reply['artifact_id']) as response:
            self.assertEqual(response.status, 404)

    def test_auth_epochs_validation_and_expiry(self):
        req = self.request('connect')
        req.pop('server_epoch', None)
        self.assertEqual(self.raw(req, token='incorrect')[0], 401)
        self.assertEqual(self.raw({**req,'version':'v1'})[0], 400)
        self.assertEqual(self.raw({**req,'expires_at':1})[0], 410)
        bad = self.request('provision', session_id=uuid4().hex, workdir={'kind':'fixture','source_id':'empty','mode':'rw'})
        bad['server_epoch']='old'
        self.assertEqual(self.raw(bad)[0], 409)

    def test_output_is_bounded(self):
        box = self.provision()
        value = self.python(box, "import sys; sys.stdout.write('x'*200000); sys.stderr.write('y'*200000)")
        self.assertEqual(value['status'], 'success')
        self.assertEqual(len(value['stdout']), 65536)
        self.assertEqual(len(value['stderr']), 65536)
        self.assertTrue(value['truncated'])

    def test_sessions_are_isolated(self):
        first, second = self.provision(source='empty'), self.provision(source='empty')
        self.assertEqual(self.run_command(first, ['sh','-c','echo private > private'])['status'], 'success')
        self.assertEqual(self.run_command(second, ['cat','private'])['status'], 'failed')

    def test_large_binary_file_and_read_limit(self):
        box = self.provision(source='empty')
        data = bytes(range(256)) * 4096
        encoded = base64.b64encode(data).decode()
        value = self.wait(self.start(box, {'name': 'WriteFile', 'path': 'binary', 'content_base64': encoded}))
        self.assertEqual(value['status'], 'success', value)
        value = self.wait(self.start(box, {'name': 'ReadFile', 'path': 'binary'}))
        self.assertEqual(base64.b64decode(value['content_base64']), data)
        self.python(box, "open('binary','ab').write(b'x')")
        value = self.wait(self.start(box, {'name': 'ReadFile', 'path': 'binary'}))
        self.assertEqual(value['status'], 'failed')
        self.assertIn('file_too_large', value['stderr'])

    def test_utf8_across_output_chunks(self):
        box = self.provision(source='empty')
        value = self.python(box, "import os; data=('x'*4095+'Я'*2000).encode(); os.write(1,data[:4096]); os.write(1,data[4096:])")
        self.assertEqual(value['status'], 'success')
        self.assertEqual(value['stdout'], 'x'*4095+'Я'*2000)

    def test_pid_limit_is_enforced(self):
        box = self.provision(source='empty')
        value = self.python(box, """import subprocess,errno
children=[]
try:
 for _ in range(300):
  children.append(subprocess.Popen(['sleep','20']))
except OSError as e:
 assert e.errno==errno.EAGAIN,e
 print('PID_LIMIT')
finally:
 for child in children: child.kill()
 for child in children: child.wait()
""")
        self.assertEqual(value['status'], 'success', value)
        self.assertEqual(value['stdout'], 'PID_LIMIT\n')

    def test_conflicting_exec_and_release_active_operation(self):
        box = self.provision(source='empty')
        container = self.container(box)
        req = self.start(box, {'name':'RunCommand','argv':['sleep','30'],'cwd':'.'})
        status, _ = self.send('exec', sandbox_id=box, operation_id=uuid4().hex,
                             command={'name':'RunCommand','argv':['true'],'cwd':'.'})
        self.assertEqual(status, 409)
        self.assertEqual(self.send('release', sandbox_id=box)[0], 200)
        self.assertEqual(self.wait(req)['status'], 'cancelled')
        with self.assertRaises(EngineError):
            self.engine.request('GET', f'/containers/{container}/json')
        # Even a new request_id must not rerun an already known operation.
        self.assertEqual(self.raw({**req, 'request_id':uuid4().hex})[0], 202)

    def test_collect_refuses_background_writers(self):
        box = self.provision(source='empty')
        value = self.python(box, "import subprocess; subprocess.Popen(['sleep','20'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)")
        self.assertEqual(value['status'], 'success')
        status, body = self.send('collect', sandbox_id=box)
        self.assertEqual(status, 409)
        self.assertEqual(body['error']['code'], 'background_processes')


if __name__ == '__main__':
    unittest.main(verbosity=2)
