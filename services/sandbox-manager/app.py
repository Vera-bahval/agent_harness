"""Internal, authenticated sandbox-manager API."""
import hmac
import json
import logging
from http.server import ThreadingHTTPServer
import signal
import threading
import time

from services.common.server import Handler as BaseHandler
from runtime.manager import Fault, Manager


class Handler(BaseHandler):
    def authorized(self):
        expected = 'Bearer ' + self.server.manager.token
        if not hmac.compare_digest(self.headers.get('Authorization', '').encode(), expected.encode()):
            raise Fault(401, 'unauthorized', 'Service token required')

    def fault(self, exc, req=None):
        identifier = req.get('request_id') if isinstance(req, dict) else None
        # Invalid request IDs must not leak into an otherwise valid error envelope.
        import re
        if not isinstance(identifier, str) or not re.fullmatch('[a-zA-Z0-9_-]{1,80}', identifier):
            identifier = None
        self.send_json(exc.status, self.server.manager.error(exc, identifier))

    def do_GET(self):
        try:
            if self.path == '/health':
                value = self.server.manager.health()
                self.send_json(200 if value['status'] == 'ready' else 503, value)
            elif self.path == '/live':
                self.send_json(200, {'service': 'sandbox-manager', 'status': 'alive', 'stage': 'sandbox'})
            elif self.path.startswith('/v0/artifacts/'):
                self.authorized()
                data = self.server.manager.artifact(self.path.removeprefix('/v0/artifacts/'))
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-tar')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(data)
            else:
                raise Fault(404, 'not_found', 'Route not found')
        except Fault as exc:
            self.fault(exc)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def do_POST(self):
        req = None
        try:
            self.authorized()
            if self.path not in ('/v0/requests', '/v0/events'):
                raise Fault(404, 'not_found', 'Route not found')
            if self.headers.get('Transfer-Encoding') or self.headers.get_content_type() != 'application/json':
                raise Fault(400, 'invalid_request', 'Expected bounded JSON request')
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2 * 1024 * 1024:
                    raise ValueError()
                req = json.loads(self.rfile.read(length))
            except (ValueError, OSError):
                raise Fault(400, 'invalid_request', 'Invalid JSON body')
            if self.path == '/v0/events':
                self.stream(req)
            else:
                status, body = self.server.manager.handle(req)
                self.send_json(status, body)
        except Fault as exc:
            self.fault(exc, req)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def stream(self, req):
        manager = self.server.manager
        events = manager.events(req)
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.connection.settimeout(5)
        end = time.monotonic() + req['timeout_s']
        heartbeat = time.monotonic()
        cursor = dict(req)
        while not manager.closing and time.monotonic() < end:
            for event in events:
                data = f"id: {event['event_id']}\nevent: {event['type']}\ndata: {json.dumps(event)}\n\n"
                self.wfile.write(data.encode())
                self.wfile.flush()
                cursor['after_event_id'] = event['event_id']
            if time.monotonic() - heartbeat >= 15:
                self.wfile.write(b': ping\n\n')
                self.wfile.flush()
                heartbeat = time.monotonic()
            manager.stop_event.wait(.05)
            try:
                events = manager.events(cursor, validate=False)
            except Fault:
                # After headers, close rather than append a second HTTP response.
                return


def main():
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    manager = Manager()
    manager.start()
    server = ThreadingHTTPServer(('0.0.0.0', 8000), Handler)
    server.manager = manager
    server.daemon_threads = True

    def stop(signum, frame):
        manager.closing = True
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logging.info(json.dumps({'event': 'listening', 'service': 'sandbox-manager', 'stage': 'sandbox'}))
    try:
        server.serve_forever(poll_interval=.2)
    finally:
        server.server_close()
        manager.close()
        logging.info(json.dumps({'event': 'stopped', 'service': 'sandbox-manager'}))


if __name__ == '__main__':
    main()
