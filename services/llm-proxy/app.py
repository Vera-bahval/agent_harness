import json
from services.common.pairs import load_pairs
from services.common.server import Handler, Settings, serve
from runtime.proxy import ModelError, Proxy


class ProxyHandler(Handler):
    def do_POST(self):
        if self.path!='/internal/completions':
            return super().do_POST()
        try:
            length=int(self.headers.get('Content-Length','0'))
            if self.headers.get('Transfer-Encoding') or not 0<length<=524288:
                raise ValueError()
            payload=json.loads(self.rfile.read(length))
            value=self.server.app.complete(payload)
            self.send_json(200,value)
        except ModelError as exc:
            self.send_json(exc.status,{'error':{'code':exc.code,'class':'model_error'}})
        except (ValueError,TypeError,KeyError,AttributeError):
            self.send_json(400,{'error':{'code':'invalid_request','class':'protocol_error'}})
        except OSError:
            self.send_json(503,{'error':{'code':'proxy_unavailable','class':'model_error'}})


if __name__=='__main__':
    serve(Settings('llm-proxy',{}),handler_class=ProxyHandler,app=Proxy(load_pairs()))
