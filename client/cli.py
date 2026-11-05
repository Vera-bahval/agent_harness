"""Standalone HTTP/SSE client; never starts local tools or Docker containers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from uuid import uuid4


class ClientError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None


class Client:
    def __init__(self,url,token):
        self.url=url.rstrip('/')
        self.token=token
        self.epoch=None
        self.opener=build_opener(ProxyHandler({}),NoRedirect())

    def payload(self,kind,timeout=10,**fields):
        value={'version':'v0','type':kind,'request_id':uuid4().hex,'expires_at':int(time.time())+3600,'timeout_s':timeout,**fields}
        if kind!='connect': value['server_epoch']=self.epoch
        return value

    def open(self,path,data=None,timeout=15):
        request=Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,
                        headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        try: return self.opener.open(request,timeout=timeout)
        except HTTPError as exc:
            try: code=json.loads(exc.read(16384)).get('error',{}).get('code','http_error')
            except (ValueError,AttributeError): code='http_error'
            raise ClientError(str(code)) from None

    def request(self,kind,timeout=10,**fields):
        data=self.payload(kind,timeout,**fields)
        # Retry delivery with EXACT same request_id and expiry; gateway deduplicates side effects.
        for attempt in range(2):
            try:
                with self.open('/v0/requests',data,timeout+5) as response: value=json.load(response)
                if kind=='connect': self.epoch=value['server_epoch']
                elif value['server_epoch']!=self.epoch: raise ClientError('stale_epoch')
                return value
            except (URLError,OSError):
                if attempt: raise ClientError('unknown_outcome') from None
                time.sleep(.2)

    def events(self,session,cursor=0,timeout=30):
        payload=self.payload('events.subscribe',timeout,session_id=session,after_event_id=cursor)
        with self.open('/v0/events',payload,timeout+5) as response:
            if not response.headers.get('Content-Type','').startswith('text/event-stream'): raise ClientError('invalid_stream')
            event={}
            while True:
                raw=response.readline(262145)
                if not raw: return
                if len(raw)>262144: raise ClientError('event_too_large')
                line=raw.decode('utf-8').rstrip('\r\n')
                if not line:
                    if 'data' in event:
                        value=json.loads(event['data'])
                        if value['server_epoch']!=self.epoch or value['session_id']!=session or str(value['event_id'])!=event.get('id'):
                            raise ClientError('invalid_event')
                        if value['event_id']>cursor:
                            cursor=value['event_id']
                            yield value
                    event={}
                elif not line.startswith(':'):
                    key,_,value=line.partition(':')
                    event[key]=value.lstrip(' ')

    def wait(self,session,run,after=0,on_event=print,timeout=240):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            try:
                for event in self.events(session,after,timeout=min(30,max(1,int(end-time.monotonic())))):
                    after=event['event_id']
                    on_event(event)
                    if event['type']=='run.result' and event['run_id']==run: return event['result']
            except ClientError as exc:
                if str(exc)!='cursor_expired': raise
                state=self.request('run.get',session_id=session,run_id=run)
                if state['state']=='terminal': return state['result']
                raise ClientError('cursor_expired: query run.get; history cannot be recovered') from None
            except (URLError,OSError):
                time.sleep(.2)
            state=self.request('run.get',session_id=session,run_id=run)
            if state['state']=='terminal': return state['result']
        raise ClientError('client_deadline')

    def download(self,metadata,path,timeout=120):
        with self.open('/v0/artifacts/'+metadata['artifact_id'],timeout=timeout) as response:
            raw=response.read(64*1024*1024+1)
        if len(raw)!=metadata['size_bytes'] or hashlib.sha256(raw).hexdigest()!=metadata['sha256']:
            raise ClientError('artifact_integrity_error')
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        Path(path).write_bytes(raw)


def main():
    parser=argparse.ArgumentParser(description='Agent Harness CLI: public v0 HTTP + SSE')
    parser.add_argument('--url',default='http://127.0.0.1:8080')
    parser.add_argument('--token-file',default='.local/client.token',help='token file; CLIENT_TOKEN env takes precedence')
    sub=parser.add_subparsers(dest='command',required=True)
    run=sub.add_parser('run',help='create session, run, stream events, collect and close')
    run.add_argument('message')
    run.add_argument('--pair',default='replay-b1')
    run.add_argument('--fixture',default='B1_1')
    run.add_argument('--timeout',type=int,default=60)
    run.add_argument('--output',default='results/local/client-result.tar')
    watch=sub.add_parser('watch',help='reconnect to an existing run with a cursor')
    watch.add_argument('session_id'); watch.add_argument('run_id'); watch.add_argument('--after',type=int,default=0)
    cancel=sub.add_parser('cancel'); cancel.add_argument('session_id'); cancel.add_argument('run_id')
    args=parser.parse_args()
    try:
        token=os.environ.get('CLIENT_TOKEN') or Path(args.token_file).read_text().strip()
        client=Client(args.url,token)
        client.request('connect')
        def show(event): print(json.dumps(event,ensure_ascii=False),flush=True)
        if args.command=='cancel':
            show(client.request('run.cancel',session_id=args.session_id,run_id=args.run_id)); return
        if args.command=='watch':
            result=client.wait(args.session_id,args.run_id,args.after,show)
            show(result)
            if result['status']!='completed': raise SystemExit(1)
            return
        sid=None
        try:
            sid=client.request('session.create',timeout=30,pair_id=args.pair,workdir={'kind':'fixture','source_id':args.fixture,'mode':'rw'})['session_id']
            accepted=client.request('run.start',timeout=args.timeout,session_id=sid,message=args.message)
            show(accepted)
            try:
                result=client.wait(sid,accepted['run_id'],on_event=show,timeout=args.timeout+60)
            except KeyboardInterrupt:
                client.request('run.cancel',session_id=sid,run_id=accepted['run_id'])
                result=client.wait(sid,accepted['run_id'],on_event=show,timeout=60)
            show(result)
            if result['status']!='completed': raise SystemExit(1)
            metadata=client.request('session.collect',timeout=30,session_id=sid)
            client.download(metadata,args.output)
            print('Artifact saved: '+args.output)
        finally:
            if sid: client.request('session.close',timeout=45,session_id=sid)
    except (ClientError,OSError,ValueError) as exc:
        # Do not print raw network errors, URLs or headers containing credentials.
        message=str(exc) if isinstance(exc,ClientError) else 'client_io_or_protocol_error'
        print('Error: '+message,file=sys.stderr)
        raise SystemExit(1)


if __name__=='__main__': main()
