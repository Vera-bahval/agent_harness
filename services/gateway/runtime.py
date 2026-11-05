"""Authenticated v0 gateway. In-memory sessions, deduplication and resumable events."""
from services.common.fixtures import SOURCES
from collections import deque
import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import threading
import time
from urllib.request import Request, build_opener, ProxyHandler
from uuid import uuid4

from jsonschema import Draft202012Validator
from services.common.http_client import request_json, RemoteError, NoRedirect


class Fault(Exception):
    def __init__(self,code,status=400,category='protocol_error'):
        self.code,self.status,self.category=code,status,category


def detail(code,category='protocol_error',retry='never'):
    return {'class':category,'code':code,'message':code,'retry':retry}


class Gateway:
    def __init__(self,tokens,pairs,schema,harness_url):
        # JSON object maps stable principal names to opaque tokens. Empty config denies all clients.
        self.tokens=json.loads(tokens or '{}')
        if not isinstance(self.tokens,dict) or any(not isinstance(k,str) or not isinstance(v,str) or len(v)<24 for k,v in self.tokens.items()):
            raise ValueError('CLIENT_TOKENS must map principals to tokens of at least 24 characters')
        if len(set(self.tokens.values()))!=len(self.tokens): raise ValueError('duplicate_client_token')
        self.pairs=pairs
        self.validator=Draft202012Validator(schema)
        self.url=harness_url.rstrip('/')
        self.epoch=uuid4().hex
        self.harness_epoch=self.remote('/internal/gateway/attach',{'epoch':self.epoch},timeout=40)['epoch']
        self.lock=threading.RLock()
        self.sessions={}
        self.cache={}
        self.artifacts={}
        self.stop=threading.Event()
        self.worker=threading.Thread(target=self.reap,daemon=True)
        self.worker.start()

    def remote(self,path,data=None,timeout=10):
        return request_json(self.url+path,data,timeout=timeout,trusted_errors=True)

    def principal(self,header):
        supplied=header[7:] if header and header.startswith('Bearer ') else ''
        for principal,token in self.tokens.items():
            if hmac.compare_digest(supplied.encode(),token.encode()): return principal
        raise Fault('unauthorized',401)

    def validate(self,data,stream=False):
        if not isinstance(data,dict) or data.get('version')!='v0': raise Fault('unsupported_version')
        if not self.validator.is_valid(data): raise Fault('invalid_request')
        allowed={'events.subscribe'} if stream else {'connect','session.create','run.start','run.get','run.cancel','session.collect','session.close'}
        if data['type'] not in allowed: raise Fault('invalid_request')
        if data['type']!='connect' and data['server_epoch']!=self.epoch: raise Fault('stale_epoch',409)
        now=int(time.time())
        if data['expires_at']<=now: raise Fault('expired_request',410)
        if data['expires_at']>now+3600: raise Fault('invalid_expiry')

    def response(self,data,kind,**fields):
        value={'version':'v0','type':kind,'request_id':data['request_id'],'server_epoch':self.epoch,**fields}
        if not self.validator.is_valid(value): raise RuntimeError('invalid_gateway_response')
        return value

    def owned(self,principal,identifier):
        session=self.sessions.get(identifier)
        if not session or session['owner']!=principal: raise Fault('not_found',404)
        session['touched']=time.monotonic()
        return session

    def emit(self,session,run,kind,**fields):
        session['seq']+=1
        value={'version':'v0','type':kind,'server_epoch':self.epoch,'session_id':session['id'],
               'run_id':run['id'],'trace_id':run['trace_id'],'event_id':session['seq'],**fields}
        if not self.validator.is_valid(value): raise RuntimeError('invalid_gateway_event')
        session['events'].append((time.monotonic(),value))
        self.trim(session)

    def trim(self,session):
        events=session['events']
        while events and (len(events)>10000 or events[0][0]<time.monotonic()-3600): events.popleft()

    def dispatch(self,principal,data):
        self.validate(data)
        with self.lock:
            key=(principal,data['request_id'])
            fingerprint=json.dumps(data,sort_keys=True)
            saved=self.cache.get(key)
            if saved:
                if saved[0]!=fingerprint: raise Fault('idempotency_conflict',409)
                return saved[2],copy.deepcopy(saved[3])
            if len(self.cache)>=10000: raise Fault('capacity',503)
            try:
                status,value=self.action(principal,data)
            except Fault as exc:
                status=exc.status
                value=self.response(data,'error',error=detail(exc.code,exc.category))
            except RemoteError:
                status=503
                value=self.response(data,'error',error=detail('harness_unavailable','transport_error','query_status'))
            self.cache[key]=(fingerprint,data['expires_at'],status,value)
            return status,copy.deepcopy(value)

    def action(self,principal,data):
        kind=data['type']
        if kind=='connect':
            return 200,self.response(data,'connected',limits={'max_timeout_s':1800,'request_window_s':3600,'event_retention_s':3600,'max_events':10000})
        if self.remote('/internal/info')['epoch']!=self.harness_epoch: raise Fault('harness_restarted',503,'transport_error')
        if kind=='session.create':
            if len(self.sessions)>=1000: raise Fault('capacity',503)
            if data['pair_id'] not in self.pairs: raise Fault('unknown_pair')
            work=data['workdir']
            if work['kind']!='fixture' or work['source_id'] not in SOURCES: raise Fault('unsupported_workdir_source')
            identifier=uuid4().hex
            self.remote('/internal/sessions',{'session_id':identifier,'pair_id':data['pair_id'],'workdir':work,'timeout_s':data['timeout_s']},timeout=min(25,data['timeout_s']+2))
            self.sessions[identifier]={'id':identifier,'owner':principal,'pair':data['pair_id'],'workdir':work,
                'state':'ready','active':None,'runs':{},'events':deque(),'seq':0,'touched':time.monotonic(),'closed_at':None}
            return 200,self.response(data,'session.created',session_id=identifier,pair_sha256=hashlib.sha256(json.dumps(self.pairs[data['pair_id']],sort_keys=True).encode()).hexdigest())
        session=self.owned(principal,data['session_id'])
        identifier=session['id']
        if kind=='run.start':
            if session['state']!='ready': raise Fault('conflict',409)
            if data['timeout_s']>self.pairs[session['pair']]['timeout_s']: raise Fault('pair_timeout_exceeded')
            rid=uuid4().hex
            # Reserve before transport: an uncertain start cannot permit a second run.
            session['state']='running'
            session['active']=rid
            run={'id':rid,'trace_id':uuid4().hex,'state':'queued','result':None,'cursor':0}
            session['runs'][rid]=run
            try:
                value=self.remote('/internal/runs',{'run_id':rid,'session_id':identifier,'pair_id':session['pair'],
                    'prompt':data['message'],'workdir':session['workdir'],'timeout_s':data['timeout_s']})
                run['trace_id']=value['trace_id']
                run['state']='running'
            except RemoteError:
                session['state']='closing'
                threading.Thread(target=self.monitor,args=(session,run),daemon=True).start()
                raise
            self.emit(session,run,'run.started')
            threading.Thread(target=self.monitor,args=(session,run),daemon=True).start()
            return 202,self.response(data,'run.accepted',session_id=identifier,run_id=rid,trace_id=run['trace_id'])
        if kind in ('run.get','run.cancel'):
            run=session['runs'].get(data['run_id'])
            if run is None: raise Fault('not_found',404)
            if kind=='run.get':
                return 200,self.response(data,'run.state',session_id=identifier,run_id=run['id'],state=run['state'],result=run['result'])
            cancel_status='already_terminal'
            if run['state']!='terminal':
                state=self.remote('/internal/runs/'+run['id']+'/cancel',{})
                if state.get('state')!='terminal':
                    run['state']='cancelling'
                    cancel_status='cancelling'
            return 202,self.response(data,'run.cancelled',session_id=identifier,run_id=run['id'],status=cancel_status)
        if kind=='session.close':
            self.close_session(session,min(120,data['timeout_s']))
            return 200,self.response(data,'session.closed',session_id=identifier)
        if kind=='session.collect':
            if session['state']!='ready': raise Fault('conflict',409)
            raw=self.fetch('/internal/sessions/'+identifier+'/collect',{'timeout_s':min(120,data['timeout_s'])},min(122,data['timeout_s']+2))
            aid=uuid4().hex
            # Bounded total retained artifacts, not only each single archive.
            if sum(len(x[1]) for x in self.artifacts.values())+len(raw)>128*1024*1024: raise Fault('artifact_capacity',503)
            self.artifacts[aid]=(identifier,raw)
            return 200,self.response(data,'session.collected',session_id=identifier,artifact_id=aid,size_bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        raise Fault('invalid_request')

    def fetch(self,path,data,timeout):
        req=Request(self.url+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})
        try:
            with build_opener(ProxyHandler({}),NoRedirect()).open(req,timeout=timeout) as response:
                raw=response.read(64*1024*1024+1)
                if len(raw)>64*1024*1024: raise Fault('artifact_too_large',503)
                return raw
        except OSError: raise Fault('collect_failed',503,'sandbox_error') from None

    def close_session(self,session,timeout):
        if session['state']=='closed': return
        session['state']='closing'
        self.remote('/internal/sessions/'+session['id']+'/close',{'timeout_s':timeout},timeout=timeout+2)
        session['state']='closed'
        session['closed_at']=time.monotonic()
        for aid,(sid,_) in list(self.artifacts.items()):
            if sid==session['id']: del self.artifacts[aid]

    def monitor(self,session,run):
        try:
            while not self.stop.is_set():
                if self.remote('/internal/info')['epoch']!=self.harness_epoch: raise RemoteError('harness_restarted')
                state=self.remote('/internal/runs/'+run['id'])
                events=self.remote('/internal/runs/'+run['id']+'/trace')['events']
                with self.lock:
                    for event in events:
                        if event['event_id']<=run['cursor']: continue
                        run['cursor']=event['event_id']
                        if event['event']=='tool.started' and event['name'] in ('RunCommand','ReadFile','WriteFile'):
                            self.emit(session,run,'tool.started',operation_id=event['operation_id'],name=event['name'])
                        elif event['event']=='tool.result':
                            self.emit(session,run,'tool.finished',operation_id=event['operation_id'],status=event['status'])
                    if state['state']=='terminal':
                        raw=state['result']
                        error=raw['error']
                        if error:
                            category=error['class'] if error['class'] in ('protocol_error','command_error','timeout','sandbox_error','transport_error') else 'protocol_error'
                            error=detail(error['code'],category)
                        self.finish(session,run,{'status':raw['status'],'answer':raw['answer'],'error':error,'usage':raw['usage'],**{k:raw[k] for k in ('model','mode') if k in raw}})
                        return
                self.stop.wait(.1)
        except Exception:
            with self.lock:
                self.finish(session,run,{'status':'failed','answer':'','error':detail('unknown_outcome','transport_error','query_status'),
                                         'usage':{'tokens_in':None,'tokens_out':None}})
                session['state']='closing'
                try: self.close_session(session,30)
                except Exception: pass

    def finish(self,session,run,result):
        if run['state']=='terminal': return
        run['state']='terminal'
        run['result']=result
        session['active']=None
        if session['state'] not in ('closing','closed'):
            session['state']='ready' if result['status']=='completed' else 'broken'
        self.emit(session,run,'run.result',result=result)

    def events(self,principal,data):
        self.validate(data,stream=True)
        with self.lock:
            session=self.owned(principal,data['session_id'])
            self.trim(session)
            cursor=data['after_event_id']
            if cursor>session['seq']: raise Fault('invalid_cursor')
            floor=session['events'][0][1]['event_id']-1 if session['events'] else session['seq']
            if cursor<floor: raise Fault('cursor_expired',410)
            return [copy.deepcopy(e) for _,e in session['events'] if e['event_id']>cursor]

    def artifact(self,principal,aid):
        with self.lock:
            entry=self.artifacts.get(aid)
            if entry is None: raise Fault('not_found',404)
            self.owned(principal,entry[0])
            return entry[1]

    def reap(self):
        while not self.stop.wait(10):
            with self.lock:
                self.cache={k:v for k,v in self.cache.items() if v[1]>time.time()}
                for sid,session in list(self.sessions.items()):
                    self.trim(session)
                    if session['closed_at'] and time.monotonic()-session['closed_at']>3600:
                        del self.sessions[sid]
                    elif session['state'] not in ('running','closed') and time.monotonic()-session['touched']>1800:
                        try: self.close_session(session,30)
                        except Exception: pass

    def close(self):
        self.stop.set()
        with self.lock:
            for session in self.sessions.values():
                try: self.close_session(session,30)
                except Exception: pass
