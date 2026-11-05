"""python -m baf.runner --pair replay-b1 --tasks B1.1 --repeats 1"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4

from client.cli import Client
from services.common.pairs import load_pairs
from .core import discover,execute


def main():
    parser=argparse.ArgumentParser(description='BAF runner via public gateway; checker executes on host')
    parser.add_argument('--pair',default='replay-b1')
    parser.add_argument('--tasks',nargs='+',default=['B1.1'])
    parser.add_argument('--repeats',type=int,default=1)
    parser.add_argument('--mode',choices=['smoke','smoke-all','quality'],default='smoke')
    parser.add_argument('--url',default='http://127.0.0.1:8080')
    parser.add_argument('--token-file',default='.local/client.token')
    parser.add_argument('--output',default='results/baf')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    try:
        tasks=discover(root/'baf');pairs=load_pairs(root/'configs/pairs');pair=pairs[args.pair]
        selected=list(tasks.values()) if args.tasks==['all'] else [tasks[key] for key in args.tasks]
        if len({t.id for t in selected})!=len(selected) or not 1<=args.repeats<=100: raise ValueError('invalid_selection_or_repeats')
        if args.mode=='quality' and pair['mode']!='real': raise ValueError('quality_requires_real_pair')
        if args.mode=='smoke': selected=[t for t in selected if t.metadata['level']=='simple']
        if not selected: raise ValueError('empty_selection')
        token=os.environ.get('CLIENT_TOKEN') or Path(args.token_file).read_text().strip()
    except (KeyError,ValueError,OSError): parser.error('Invalid task/pair/token configuration; check task.yaml, registry, pair and token file')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    output=Path(args.output)/args.pair/stamp;output.mkdir(parents=True,exist_ok=False)
    revision=subprocess.run(['git','rev-parse','HEAD'],cwd=root,capture_output=True,text=True).stdout.strip() or None
    dirty=bool(subprocess.run(['git','status','--porcelain'],cwd=root,capture_output=True,text=True).stdout)
    provenance={'git_revision':revision,'git_dirty':dirty,'measurement':args.mode,'started_at':datetime.now(timezone.utc).isoformat()}
    rows=[]
    for task in selected:
        for repeat in range(1,args.repeats+1):
            client=Client(args.url,token)
            row=execute(client,task,pair,repeat,output,provenance)
            with (output/'results.jsonl').open('a') as f: f.write(json.dumps(row,ensure_ascii=False)+'\n')
            rows.append(row)
            print(f"{task.id} repeat={repeat}: {row['outcome']} ({row['reason']}); cleanup={row['cleanup']}",flush=True)
    summary={'pair':args.pair,'mode':pair['mode'],'measurement':args.mode,'total':len(rows),
             'passed':sum(row['pass'] for row in rows),'outcomes':{name:sum(r['outcome']==name for r in rows) for name in sorted({r['outcome'] for r in rows})}}
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False))
    print('Results: '+str(output))
    raise SystemExit(0 if summary['passed']==summary['total'] else 1)


if __name__=='__main__': main()
