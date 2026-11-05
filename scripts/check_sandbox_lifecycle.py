"""Lifecycle integration: crash recovery and compose down cleanup.

Run only on a development Compose project without active sandbox sessions.
The test restarts this project's services and leaves them running on success.
"""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def docker(*args, stdin=None):
    completed = subprocess.run(['docker', *args], input=stdin, text=True, cwd=ROOT,
                               capture_output=True, timeout=120, check=True)
    return completed.stdout


PROVISION = '''
import json,time
from pathlib import Path
from uuid import uuid4
from urllib.request import Request,urlopen
headers={'Content-Type':'application/json','Authorization':'Bearer '+Path('/run/runtime-auth/sandbox.token').read_text().strip()}
def call(data):
 req=Request('http://127.0.0.1:8000/v0/requests',data=json.dumps(data).encode(),headers=headers)
 with urlopen(req,timeout=25) as response:return json.load(response)
base={'version':'v0','request_id':uuid4().hex,'expires_at':int(time.time())+180,'timeout_s':20}
epoch=call(dict(base,type='connect'))['server_epoch']
base.update(server_epoch=epoch,request_id=uuid4().hex)
box=call(dict(base,type='provision',session_id=uuid4().hex,workdir={'kind':'fixture','source_id':'empty','mode':'rw'}))['sandbox_id']
base.update(request_id=uuid4().hex)
call(dict(base,type='exec',sandbox_id=box,operation_id=uuid4().hex,command={'name':'RunCommand','argv':['sleep','90'],'cwd':'.'},timeout_s=120))
print(json.dumps({'box':box,'epoch':epoch}))
'''


def main():
    config = json.loads(docker('compose', 'config', '--format', 'json'))
    project = config['services']['sandbox-manager']['environment']['SANDBOX_PROJECT']
    label = 'agent_harness.owner='+project

    def sandboxes():
        return docker('ps', '-aq', '--filter', 'label='+label).split()

    assert not sandboxes(), 'Release existing sandbox sessions before lifecycle tests'
    old = json.loads(docker('compose', 'exec', '-T', 'sandbox-manager', 'python', '-', stdin=PROVISION))
    assert len(sandboxes()) == 1, 'Test sandbox missing'
    docker('compose', 'kill', '-s', 'SIGKILL', 'sandbox-manager')
    assert len(sandboxes()) == 1, 'Expected an orphan after SIGKILL'
    docker('compose', 'up', '-d', '--wait', '--wait-timeout', '90')
    assert not sandboxes(), 'Startup did not clean the orphan'
    check_epoch = '''
import json,time,sys
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from uuid import uuid4
payload={'version':'v0','type':'release','request_id':uuid4().hex,'server_epoch':sys.argv[1],
         'sandbox_id':sys.argv[2],'expires_at':int(time.time())+60,'timeout_s':10}
req=Request('http://127.0.0.1:8000/v0/requests',data=json.dumps(payload).encode(),headers={
 'Content-Type':'application/json','Authorization':'Bearer '+Path('/run/runtime-auth/sandbox.token').read_text().strip()})
try:
 urlopen(req,timeout=15)
 raise AssertionError('Old epoch accepted')
except HTTPError as e:
 assert e.code==409
 assert json.load(e)['error']['code']=='stale_epoch'
'''
    docker('compose', 'exec', '-T', 'sandbox-manager', 'python', '-c', check_epoch, old['epoch'], old['box'])
    print('SIGKILL -> restart: orphan removed, old epoch rejected')
    docker('compose', 'exec', '-T', 'sandbox-manager', 'python', '-', stdin=PROVISION)
    assert len(sandboxes()) == 1
    docker('compose', 'down', '-v', '--remove-orphans')
    assert not sandboxes(), 'Graceful compose down left a sandbox'
    project_filter = 'label=com.docker.compose.project='+config['name']
    assert not docker('ps', '-aq', '--filter', project_filter).strip()
    assert not docker('volume', 'ls', '-q', '--filter', project_filter).strip()
    assert not docker('network', 'ls', '-q', '--filter', project_filter).strip()
    print('compose down -v: active sandbox, containers, volumes and networks removed')
    docker('compose', 'up', '-d', '--wait', '--wait-timeout', '90')
    print('Server restarted and healthy')


if __name__ == '__main__':
    main()
