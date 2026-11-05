"""Build/run an isolated working-tree snapshot without .env, .venv, Git data or prior results."""
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]


def main():
    project='cp1_clean_'+uuid4().hex[:10]
    proof=ROOT/'results/cp1-10';proof.mkdir(parents=True,exist_ok=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    manifest=[];commands=[]
    with tempfile.TemporaryDirectory(prefix='agent-harness-clean-') as temporary:
        snapshot=Path(temporary)
        for name in subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT).decode().split('\0'):
            if not name or Path(name).parts[0]=='results':continue
            source=ROOT/name
            if not source.is_file() or source.is_symlink():continue
            destination=snapshot/name;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)
            manifest.append([name,hashlib.sha256(source.read_bytes()).hexdigest()])
        token=secrets.token_urlsafe(32)
        (snapshot/'.env').write_text('COMPOSE_PROJECT_NAME='+project+'\nGATEWAY_PORT='+str(port)+'\nLLM_MODE=replay\nCLIENT_TOKENS='+json.dumps({'clean':token})+'\n')
        local=snapshot/'.local';local.mkdir();(local/'client.token').write_text(token)
        log=proof/'clean.log'
        def run(args,timeout=180):
            commands.append(args)
            with log.open('a') as output:
                result=subprocess.run(args,cwd=snapshot,stdout=output,stderr=subprocess.STDOUT,timeout=timeout)
            if result.returncode:raise RuntimeError('clean_command_failed: '+args[0])
        log.write_text('Isolated working-tree snapshot; no provider key; a separate Compose project and port.\n')
        status='failed'
        try:
            run(['docker','compose','up','--build','-d','--wait','--wait-timeout','90'])
            run([sys.executable,'-m','client.cli','--url',f'http://127.0.0.1:{port}','run','На каком порту слушает сервис? Запиши число в answer.txt.','--pair','replay-b1'])
            run([sys.executable,'-m','baf.runner','--url',f'http://127.0.0.1:{port}','--pair','replay-cp1','--tasks','all','--mode','smoke-all'],timeout=180)
            result=next((snapshot/'results/baf/replay-cp1').glob('*/summary.json'))
            summary=json.loads(result.read_text());assert summary['passed']==summary['total']==15
            run(['docker','compose','down','-v','--remove-orphans'])
            orphan=subprocess.check_output(['docker','ps','-aq','--filter','label=agent_harness.owner='+project],text=True).strip()
            assert not orphan,'Dynamic sandbox containers leaked'
            status='passed'
        finally:
            try:run(['docker','compose','down','-v','--remove-orphans'])
            finally:
                (proof/'clean.json').write_text(json.dumps({'status':status,'project':project,'snapshot_sha256':hashlib.sha256(json.dumps(sorted(manifest)).encode()).hexdigest(),'files':len(manifest),'source':'local working-tree snapshot, not remote clone','commands':commands},indent=2)+'\n')
    print('Clean snapshot: PASS; CLI connected; 15/15 replay; containers and volumes removed')


if __name__=='__main__':main()
