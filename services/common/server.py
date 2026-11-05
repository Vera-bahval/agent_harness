"""HTTP bootstrap shared by the four services; agent operations are not implemented."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import signal
import threading
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

LOG = logging.getLogger('runtime')


@dataclass(frozen=True)
class Settings:
    service: str
    dependencies: dict[str, str]
    dependency_timeout: float = 2.0


def check_dependency(url: str, timeout: float) -> bool:
    # Internal health traffic must not be routed through environment HTTP proxies.
    try:
        with build_opener(ProxyHandler({})).open(url.rstrip('/') + '/health', timeout=timeout) as response:
            data = json.loads(response.read(16384))
            return response.status == 200 and data.get('status') == 'ready'
    except (URLError, OSError, ValueError, AttributeError):
        return False


def readiness(settings: Settings) -> dict:
    with ThreadPoolExecutor(max_workers=max(1, len(settings.dependencies))) as executor:
        pending = {name: executor.submit(check_dependency, url, settings.dependency_timeout)
                   for name, url in settings.dependencies.items()}
        dependencies = {name: 'ready' if task.result() else 'unavailable'
                        for name, task in pending.items()}
    return {'service': settings.service,
            'status': 'ready' if all(value == 'ready' for value in dependencies.values()) else 'not_ready',
            'stage': 'bootstrap', 'dependencies': dependencies}


class ServiceServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, settings: Settings, handler_class=None, app=None):
        self.settings = settings
        self.epoch = uuid4().hex
        self.app = app
        super().__init__(address, handler_class or Handler)


class Handler(BaseHTTPRequestHandler):
    # Close each HTTP/1.0 connection; unimplemented POST bodies are never consumed.
    server_version = 'AgentRuntime/0'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, format, *args):
        # Do not log raw paths, headers, request bodies, environment or credentials.
        pass

    def send_json(self, status: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path == '/live':
            self.send_json(200, {'service': self.server.settings.service, 'status': 'alive', 'stage': 'bootstrap'})
        elif self.path == '/health':
            state = readiness(self.server.settings)
            self.send_json(200 if state['status'] == 'ready' else 503, state)
        else:
            self.send_error_json(404, 'not_found', 'Route not found')

    def send_error_json(self, status: int, code: str, message: str):
        self.send_json(status, {'version': 'v0', 'type': 'error', 'request_id': None,
                               'server_epoch': self.server.epoch,
                               'error': {'class': 'protocol_error', 'code': code,
                                         'message': message, 'retry': 'never'}})

    def do_POST(self):
        if self.path in ('/v0/requests', '/v0/events'):
            self.send_error_json(501, 'not_implemented', 'Bootstrap only; agent operations are not implemented')
        else:
            self.send_error_json(404, 'not_found', 'Route not found')


def serve(settings: Settings, handler_class=None, app=None):
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    server = ServiceServer(('0.0.0.0', 8000), settings, handler_class, app)

    def stop(signum, frame):
        LOG.info(json.dumps({'event': 'stopping', 'service': settings.service}))
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    LOG.info(json.dumps({'event': 'listening', 'service': settings.service,
                         'port': 8000, 'stage': 'bootstrap',
                         'dependencies': list(settings.dependencies)}))
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        if app is not None and hasattr(app, 'close'):
            app.close()
        LOG.info(json.dumps({'event': 'stopped', 'service': settings.service}))
