"""Small Docker Engine HTTP client over the local Unix socket (no Docker CLI)."""
import http.client
import json
import socket
import struct
import time
from urllib.parse import quote, urlencode


class EngineError(Exception):
    def __init__(self, status=503):
        self.status = status
        super().__init__(f'Docker Engine request failed ({status})')


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, timeout):
        super().__init__('localhost', timeout=timeout)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect('/var/run/docker.sock')


class Engine:
    def request(self, method, path, body=None, *, timeout=10, raw=False, limit=70 * 1024 * 1024):
        connection = UnixConnection(max(0.05, timeout))
        headers = {}
        if isinstance(body, bytes):
            headers['Content-Type'] = 'application/x-tar'
        elif body is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(body).encode()
        try:
            connection.request(method, '/v1.45' + path, body, headers)
            response = connection.getresponse()
            if response.status >= 400:
                raise EngineError(response.status)
            data = response.read(limit + 1)
            if len(data) > limit:
                raise EngineError(413)
            return data if raw else (json.loads(data) if data else None)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            raise EngineError() from exc
        finally:
            connection.close()

    def containers(self, project):
        filters = json.dumps({'label': [f'agent_harness.owner={project}']})
        return self.request('GET', '/containers/json?' + urlencode({'all': 'true', 'filters': filters}))

    def remove(self, container, timeout=10):
        try:
            self.request('DELETE', f'/containers/{quote(container)}?force=true&v=true', timeout=timeout)
        except EngineError as exc:
            if exc.status != 404:
                raise

    def kill(self, container, timeout=10):
        try:
            self.request('POST', f'/containers/{quote(container)}/kill', timeout=timeout)
        except EngineError as exc:
            if exc.status not in (404, 409):
                raise
        # A successful kill is not yet evidence that every process has exited.
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            state = self.request('GET', f'/containers/{quote(container)}/json', timeout=max(.05, end-time.monotonic()))
            if not state['State']['Running']:
                return
            time.sleep(.02)
        raise EngineError(504)

    def execute(self, container, argv, emit, deadline, user='10001:10001'):
        remaining = lambda: max(.05, deadline - time.monotonic())
        operation = self.request('POST', f'/containers/{container}/exec', {
            'AttachStdout': True, 'AttachStderr': True, 'Tty': False,
            'User': user, 'WorkingDir': '/work', 'Cmd': argv,
        }, timeout=remaining())['Id']
        connection = UnixConnection(remaining())
        try:
            connection.request('POST', f'/v1.45/exec/{operation}/start',
                               json.dumps({'Detach': False, 'Tty': False}), {'Content-Type': 'application/json'})
            response = connection.getresponse()
            if response.status != 200:
                raise EngineError(response.status)
            def read_exact(size):
                data = bytearray()
                while len(data) < size:
                    if time.monotonic() >= deadline:
                        raise TimeoutError()
                    chunk = response.read(size-len(data))
                    if not chunk:
                        break
                    data.extend(chunk)
                return bytes(data)
            while True:
                header = read_exact(8)
                if not header:
                    break
                if len(header) != 8:
                    raise EngineError()
                stream, length = header[0], struct.unpack('>I', header[4:])[0]
                if stream not in (1, 2) or length > 16 * 1024 * 1024:
                    raise EngineError()
                # Stream Docker frames in bounded chunks; never accumulate arbitrary output.
                while length:
                    chunk = read_exact(min(length, 4096))
                    if not chunk:
                        raise EngineError()
                    emit('stdout' if stream == 1 else 'stderr', chunk)
                    length -= len(chunk)
            state = self.request('GET', f'/exec/{operation}/json', timeout=remaining())
            if state['Running']:
                raise EngineError()
            return state['ExitCode']
        except socket.timeout as exc:
            raise TimeoutError() from exc
        except (OSError, http.client.HTTPException) as exc:
            raise EngineError() from exc
        finally:
            connection.close()
