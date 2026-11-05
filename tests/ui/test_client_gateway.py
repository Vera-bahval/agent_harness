"""Real HTTP/SSE gateway and CLI against a deterministic harness mock."""
import importlib
import json
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from uuid import uuid4

from client.cli import Client,ClientError
from services.common.pairs import load_pairs
from services.common.server import ServiceServer,Settings
from services.gateway.app import GatewayHandler
from services.gateway.runtime import Gateway,Fault

ROOT=Path(__file__).resolve().parents[2]


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.token='a'*32
        self.starts=0
        self.run_states={}
        self.closed=[]
        def remote(path,data=None,timeout=10):
            if path in ('/internal/info','/internal/gateway/attach'): return {'epoch':'harness-epoch'}
            if path=='/internal/sessions': return {'session_id':data['session_id']}
            if path.endswith('/close'): self.closed.append(path); return {'closed':True}
            if path=='/internal/runs':
                self.starts+=1
                self.run_states[data['run_id']]={'state':'running','trace_id':uuid4().hex,'result':None}
                return self.run_states[data['run_id']]
            if path.endswith('/cancel'):
                self.complete(path.split('/')[-2],'cancelled'); return {}
            if path.endswith('/trace'): return {'events':[]}
            return self.run_states[path.split('/')[-1]]
        with patch.object(Gateway,'remote',side_effect=remote):
            self.app=Gateway(json.dumps({'alice':self.token,'bob':'b'*32}),load_pairs(ROOT/'configs/pairs'),json.loads((ROOT/'contracts/client-server.v0.schema.json').read_text()),'http://mock')
        self.app.remote=remote
        server=ServiceServer(('127.0.0.1',0),Settings('gateway',{}),GatewayHandler,self.app)
        threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(self.app.close)
        self.addCleanup(server.shutdown)
        self.url='http://127.0.0.1:'+str(server.server_port)
        self.client=Client(self.url,self.token)
        self.client.request('connect')

    def complete(self,rid,status='completed'):
        self.run_states[rid]['result']={'status':status,'answer':'done' if status=='completed' else '',
            'error':None if status=='completed' else {'code':'cancelled','class':'protocol_error'},'usage':{'tokens_in':None,'tokens_out':None}}
        self.run_states[rid]['state']='terminal'

    def session(self):
        return self.client.request('session.create',pair_id='replay-b1',workdir={'kind':'fixture','source_id':'B1_1','mode':'rw'})['session_id']

    def test_unauthorized_and_cross_principal_session(self):
        with self.assertRaisesRegex(ClientError,'unauthorized'): Client(self.url,'wrong').request('connect')
        sid=self.session()
        bob=Client(self.url,'b'*32);bob.request('connect')
        with self.assertRaisesRegex(ClientError,'not_found'): bob.request('session.close',session_id=sid)
        aid=uuid4().hex
        self.app.artifacts[aid]=(sid,b'archive')
        with self.assertRaisesRegex(ClientError,'not_found'): bob.open('/v0/artifacts/'+aid)
        with self.assertRaisesRegex(ClientError,'not_found'): list(bob.events(sid,timeout=1))

    def test_exact_retry_dedup_conflict_and_active_run(self):
        sid=self.session()
        data=self.client.payload('run.start',session_id=sid,message='порт')
        with self.client.open('/v0/requests',data) as response: first=json.load(response)
        with self.client.open('/v0/requests',data) as response: self.assertEqual(json.load(response),first)
        self.assertEqual(self.starts,1)
        with self.assertRaisesRegex(ClientError,'idempotency_conflict'): self.client.open('/v0/requests',{**data,'message':'different'})
        with self.assertRaisesRegex(ClientError,'conflict'): self.client.request('run.start',session_id=sid,message='second')
        self.complete(first['run_id'])
        self.client.wait(sid,first['run_id'],on_event=lambda e:None)

    def test_stream_reconnect_uses_cursor_and_one_result(self):
        sid=self.session();run=self.client.request('run.start',session_id=sid,message='порт')
        stream=self.client.events(sid,timeout=1)
        first=next(stream);stream.close()  # disconnect before completion
        self.assertEqual(first['type'],'run.started')
        self.complete(run['run_id'])
        events=[]
        result=self.client.wait(sid,run['run_id'],after=first['event_id'],on_event=events.append)
        self.assertEqual(result['status'],'completed')
        self.assertEqual([e['type'] for e in events],['run.result'])
        self.assertEqual(self.starts,1)

    def test_cancel_and_close(self):
        sid=self.session();run=self.client.request('run.start',session_id=sid,message='порт')
        self.client.request('run.cancel',session_id=sid,run_id=run['run_id'])
        self.assertEqual(self.client.wait(sid,run['run_id'],on_event=lambda e:None)['status'],'cancelled')
        self.client.request('session.close',session_id=sid)
        self.client.request('session.close',session_id=sid)
        self.assertEqual(len(self.closed),1)
        with self.assertRaisesRegex(ClientError,'conflict'): self.client.request('run.start',session_id=sid,message='after close')

    def test_cancel_after_harness_finish_before_gateway_poll(self):
        sid=self.session();run=self.client.request('run.start',session_id=sid,message='порт')
        self.complete(run['run_id'])
        original=self.app.remote
        def remote(path,data=None,timeout=10):
            if path.endswith('/cancel'): return self.run_states[run['run_id']]
            return original(path,data,timeout)
        self.app.remote=remote
        result=self.client.request('run.cancel',session_id=sid,run_id=run['run_id'])
        self.assertEqual(result['status'],'already_terminal')

    def test_cursor_expiry_and_future_cursor(self):
        sid=self.session()
        session=self.app.sessions[sid]
        session['seq']=5
        with self.assertRaisesRegex(ClientError,'cursor_expired'): list(self.client.events(sid,0,1))
        with self.assertRaisesRegex(ClientError,'invalid_cursor'): list(self.client.events(sid,6,1))

    def test_stale_epoch_expired_and_bad_direction(self):
        data=self.client.payload('session.create',pair_id='replay-b1',workdir={'kind':'fixture','source_id':'B1_1','mode':'rw'})
        for changed,code in [({'server_epoch':'old'},'stale_epoch'),({'expires_at':1},'expired_request'),({'extra':'secret'},'invalid_request')]:
            with self.assertRaisesRegex(ClientError,code): self.client.open('/v0/requests',{**data,**changed})
        with self.assertRaisesRegex(ClientError,'invalid_request'):
            self.client.open('/v0/requests',{'version':'v0','type':'session.closed','request_id':'x','server_epoch':self.app.epoch,'session_id':'x'})

    def test_duplicate_sse_is_not_delivered_twice(self):
        # CLI deduplicates within a stream; transport errors reconnect from last delivered id.
        sid=self.session();run=self.client.request('run.start',session_id=sid,message='порт')
        self.complete(run['run_id'])
        self.client.wait(sid,run['run_id'],on_event=lambda e:None)
        session=self.app.sessions[sid]
        first=session['events'][0]
        session['events'].appendleft(first)
        events=list(self.client.events(sid,0,1))
        self.assertEqual(len({e['event_id'] for e in events}),len(events))


if __name__=='__main__': unittest.main()
