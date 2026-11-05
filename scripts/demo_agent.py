"""Execute one B1.1 run through the internal harness API; no scripted tool plan."""
import io
import json
from pathlib import Path
import sys
import tarfile
import time
from urllib.request import Request, urlopen
from uuid import uuid4

URL='http://harness:8000'


def request(path,data=None):
    req=Request(URL+path,data=json.dumps(data).encode() if data is not None else None,
                headers={'Content-Type':'application/json'})
    with urlopen(req,timeout=10) as response:
        return json.load(response)


def main():
    pair=sys.argv[1] if len(sys.argv)>1 else 'replay-b1'
    identifier=uuid4().hex
    request('/internal/runs',{'run_id':identifier,'pair_id':pair,
        'prompt':'На каком порту слушает сервис? Запиши только число в файл answer.txt в корне рабочей папки.',
        'workdir':{'kind':'fixture','source_id':'B1_1','mode':'rw'},'timeout_s':60 if pair.startswith('replay') else 180})
    end=time.monotonic()+220
    while time.monotonic()<end:
        state=request('/internal/runs/'+identifier)
        if state['state']=='terminal':
            records=Path('/app/results/runs')
            records.mkdir(exist_ok=True)
            (records/(identifier+'.json')).write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
            print(json.dumps(state,ensure_ascii=False,indent=2))
            if state['result']['status']!='completed':
                raise SystemExit(1)
            with urlopen(URL+'/internal/runs/'+identifier+'/artifact',timeout=10) as response:
                raw=response.read(64*1024*1024+1)
            with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
                answer=archive.extractfile('answer.txt').read().decode().strip()
            if answer!='8137':
                raise SystemExit('B1.1 checker: answer does not match expected port')
            print('B1.1 file check: PASS (answer.txt = 8137)')
            return
        time.sleep(.1)
    request('/internal/runs/'+identifier+'/cancel',{})
    raise SystemExit('Harness deadline exceeded')


if __name__=='__main__':
    main()
