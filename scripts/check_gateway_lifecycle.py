"""Dev-only: kill gateway during a replay run; new epoch must release the old sandbox."""
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from client.cli import Client,ClientError


def docker(*args):
    return subprocess.check_output(['docker',*args],text=True,timeout=100).strip()


def main():
    # Discover only the project label, not container environment or credentials.
    cid=docker('compose','ps','-q','gateway')
    project=docker('inspect','--format','{{index .Config.Labels "com.docker.compose.project"}}',cid)
    def boxes(): return docker('ps','-aq','--filter','label=agent_harness.owner='+project)
    if boxes(): raise SystemExit('Refusing lifecycle test with existing sandboxes')
    client=Client('http://127.0.0.1:8080',Path('.local/client.token').read_text().strip())
    client.request('connect')
    sid=client.request('session.create',timeout=30,pair_id='replay-slow',workdir={'kind':'fixture','source_id':'B1_1','mode':'rw'})['session_id']
    try:
        run=client.request('run.start',timeout=60,session_id=sid,message='slow')
        stream=client.events(sid,timeout=10)
        for event in stream:
            if event['type']=='tool.started': break
        stream.close()
        assert boxes(), 'No sandbox to test'
        docker('compose','kill','-s','SIGKILL','gateway')
        docker('compose','up','-d','--no-deps','--wait','--wait-timeout','90','gateway')
        try: client.request('run.get',session_id=sid,run_id=run['run_id'])
        except ClientError as exc: assert str(exc)=='stale_epoch', str(exc)
        else: raise AssertionError('Old epoch accepted')
        assert not boxes(), 'Sandbox leaked after gateway attach'
        client.request('connect')
        print('Gateway crash recovery: PASS; old epoch rejected, sandbox released')
    finally:
        docker('compose','up','-d','--no-deps','--wait','--wait-timeout','90','gateway')
        try: client.request('session.close',timeout=45,session_id=sid)
        except ClientError: pass


if __name__=='__main__': main()
