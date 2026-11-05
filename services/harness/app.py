"""Internal run API; the authenticated client gateway comes in CP1-06."""
import json
import os
import re
from services.common.pairs import load_pairs
from services.common.server import Handler, Settings, serve
from runtime.agent import AgentService


class AgentHandler(Handler):
    def do_GET(self):
        if self.path=='/internal/info':
            return self.send_json(200,{'epoch':self.server.app.epoch})

        match=re.fullmatch(r'/internal/runs/([a-f0-9]{32})(/(trace|artifact))?',self.path)
        if not match:
            return super().do_GET()
        identifier, kind=match[1],match[3]
        try:
            state=self.server.app.state(identifier)
            if kind=='trace':
                path=self.server.app.root/'traces'/f'{identifier}.jsonl'
                # Writes are under this run lock; don't read a partially written line.
                run=self.server.app.runs[identifier]
                with run.lock:
                    events=[json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
                self.send_json(200,{'events':events})
            elif kind=='artifact':
                if state['state']!='terminal' or not state['result']['artifact_available']:
                    raise KeyError(identifier)
                data=(self.server.app.root/'artifacts'/f'{identifier}.tar').read_bytes()
                self.send_response(200)
                self.send_header('Content-Type','application/x-tar')
                self.send_header('Content-Length',str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_json(200,state)
        except (KeyError,FileNotFoundError):
            self.send_json(404,{'error':{'code':'not_found'}})

    def do_POST(self):
        if self.path in ('/internal/sessions','/internal/gateway/attach') or re.fullmatch(r'/internal/sessions/[a-f0-9]{32}/(close|collect)',self.path):
            try:
                length=int(self.headers.get('Content-Length','0'))
                if self.headers.get('Transfer-Encoding') or not 0<length<=262144: raise ValueError()
                payload=json.loads(self.rfile.read(length))
                if self.path=='/internal/gateway/attach':
                    return self.send_json(200,self.server.app.attach_gateway(payload['epoch']))
                if self.path=='/internal/sessions':
                    return self.send_json(200,self.server.app.create_session(payload))
                identifier,kind=self.path.split('/')[-2:]
                if kind=='close':
                    return self.send_json(200,self.server.app.close_session(identifier,payload.get('timeout_s',30)))
                raw=self.server.app.collect_session(identifier,payload.get('timeout_s',30))
                self.send_response(200)
                self.send_header('Content-Type','application/x-tar')
                self.send_header('Content-Length',str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except KeyError:
                self.send_json(404,{'error':{'code':'not_found'}})
            except Exception:
                self.send_json(409,{'error':{'code':'session_unavailable'}})
            return

        if self.path=='/internal/runs':
            try:
                length=int(self.headers.get('Content-Length','0'))
                if self.headers.get('Transfer-Encoding') or not 0<length<=262144:
                    raise ValueError('invalid_request')
                payload=json.loads(self.rfile.read(length))
                value=self.server.app.start(payload)
                self.send_json(202,value)
            except (ValueError,TypeError):
                self.send_json(400,{'error':{'code':'invalid_run_request'}})
        else:
            match=re.fullmatch(r'/internal/runs/([a-f0-9]{32})/cancel',self.path)
            if not match:
                return super().do_POST()
            try:
                self.send_json(202,self.server.app.cancel_run(match[1]))
            except KeyError:
                self.send_json(404,{'error':{'code':'not_found'}})


if __name__=='__main__':
    settings=Settings('harness',{'llm-proxy':os.environ.get('LLM_URL','http://llm-proxy:8000'),
                                'sandbox-manager':os.environ.get('SANDBOX_URL','http://sandbox-manager:8000')})
    serve(settings,handler_class=AgentHandler,app=AgentService(load_pairs()))
