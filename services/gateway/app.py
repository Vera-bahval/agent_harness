"""Development HTTP/SSE gateway; all v0 routes require a client token."""
import json
import os
from pathlib import Path
import re
import time
from services.common.pairs import load_pairs
from services.common.server import Handler, Settings, serve
from services.gateway.runtime import Gateway, Fault, detail


class GatewayHandler(Handler):
    def failure(self,exc,data=None):
        identifier=data.get('request_id') if isinstance(data,dict) else None
        if not isinstance(identifier,str) or not re.fullmatch('[a-zA-Z0-9_-]{1,80}',identifier): identifier=None
        self.send_json(exc.status,{'version':'v0','type':'error','request_id':identifier,
            'server_epoch':self.server.app.epoch,'error':detail(exc.code,exc.category)})

    def do_GET(self):
        if self.path in ('/health','/live'): return super().do_GET()
        try:
            principal=self.server.app.principal(self.headers.get('Authorization'))
            match=re.fullmatch('/v0/artifacts/([a-f0-9]{32})',self.path)
            if not match: raise Fault('not_found',404)
            raw=self.server.app.artifact(principal,match[1])
            self.send_response(200)
            self.send_header('Content-Type','application/x-tar')
            self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(raw)
        except Fault as exc: self.failure(exc)
        except (BrokenPipeError,ConnectionResetError,TimeoutError): pass

    def do_POST(self):
        data=None
        try:
            principal=self.server.app.principal(self.headers.get('Authorization'))
            if self.path not in ('/v0/requests','/v0/events'): raise Fault('not_found',404)
            length=int(self.headers.get('Content-Length','0'))
            if self.headers.get('Transfer-Encoding') or not 0<length<=262144: raise Fault('invalid_request')
            data=json.loads(self.rfile.read(length))
            if self.path=='/v0/events':
                pending=self.server.app.events(principal,data)
                return self.stream(principal,data,pending)
            status,value=self.server.app.dispatch(principal,data)
            self.send_json(status,value)
        except Fault as exc: self.failure(exc,data)
        except (ValueError,TypeError): self.failure(Fault('invalid_request'),data)
        except (BrokenPipeError,ConnectionResetError,TimeoutError): pass

    def stream(self,principal,data,pending):
        self.send_response(200)
        self.send_header('Content-Type','text/event-stream; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.end_headers()
        deadline=time.monotonic()+data['timeout_s']
        heartbeat=time.monotonic()+15
        while time.monotonic()<deadline and not self.server.app.stop.is_set():
            for event in pending:
                self.wfile.write(('id: '+str(event['event_id'])+'\nevent: '+event['type']+'\ndata: '+json.dumps(event,ensure_ascii=False)+'\n\n').encode())
                self.wfile.flush()
                data={**data,'after_event_id':event['event_id']}
            if pending and pending[-1]['type']=='run.result': return
            if time.monotonic()>=heartbeat:
                self.wfile.write(b': ping\n\n')
                self.wfile.flush()
                heartbeat=time.monotonic()+15
            self.server.app.stop.wait(.1)
            try: pending=self.server.app.events(principal,data)
            except Fault: return  # Headers already sent; reconnect obtains a structured error.


if __name__=='__main__':
    url=os.environ.get('HARNESS_URL','http://harness:8000')
    schema=json.loads(Path('/app/contracts/client-server.v0.schema.json').read_text())
    app=Gateway(os.environ.get('CLIENT_TOKENS',''),load_pairs(),schema,url)
    serve(Settings('gateway',{'harness':url},dependency_timeout=3),handler_class=GatewayHandler,app=app)
