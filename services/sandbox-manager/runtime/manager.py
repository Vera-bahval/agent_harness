"""Session sandbox lifecycle and v0 state machine. No user commands run here."""
from services.common.fixtures import SOURCES
import base64
import codecs
from collections import deque
from dataclasses import dataclass, field
import hashlib
import io
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import tarfile
import threading
import time
from uuid import uuid4

from jsonschema import Draft202012Validator
from .docker_api import Engine, EngineError

LOG = logging.getLogger('manager')
MAX_ARCHIVE = 64 * 1024 * 1024
LIMITS = {'max_timeout_s': 1800, 'request_window_s': 3600, 'event_retention_s': 3600, 'max_events': 10000}
REQUESTS = {'connect', 'provision', 'exec', 'operation.get', 'cancel', 'collect', 'release', 'events.subscribe'}


class Fault(Exception):
    def __init__(self, status, code, message, category='protocol_error', retry='never'):
        self.status, self.code, self.message = status, code, message
        self.category, self.retry = category, retry
        super().__init__(message)


def size(value):
    match = re.fullmatch(r'([1-9][0-9]*)([kmg]?)', value.lower())
    if not match:
        raise ValueError('Invalid resource size')
    return int(match[1]) * (1024 ** {'': 0, 'k': 1, 'm': 2, 'g': 3}[match[2]])


def result(status, *, code=None, category='command_error', stdout='', stderr='', exit_code=None,
           content=None, truncated=False):
    error = None if status == 'success' else {
        'class': category, 'code': code or status, 'message': code or status,
        'retry': 'query_status' if category in ('timeout', 'sandbox_error') else 'never'}
    return {'status': status, 'stdout': stdout, 'stderr': stderr, 'exit_code': exit_code,
            'content_base64': content, 'truncated': truncated, 'error': error}


def fixture_archive(source, mode):
    """Only trusted fixture directories are registered; never include check.py."""
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        if source is not None:
            for path in sorted(source.rglob('*')):
                if path.is_symlink() or not (path.is_file() or path.is_dir()):
                    raise Fault(400, 'invalid_fixture', 'Fixture contains a special file')
                info = archive.gettarinfo(str(path), arcname=str(path.relative_to(source)))
                info.uid = info.gid = 0 if mode == 'ro' else 10001
                info.uname = info.gname = ''
                info.mode = (0o555 if info.isdir() else 0o444) if mode == 'ro' else (0o700 if info.isdir() else 0o600)
                if info.isfile():
                    with path.open('rb') as stream:
                        archive.addfile(info, stream)
                else:
                    archive.addfile(info)
    return output.getvalue()


def safe_archive(raw):
    """Validate daemon archive and remove its outer /work directory, without extraction."""
    output = io.BytesIO()
    seen = set()
    total = 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as source, tarfile.open(fileobj=output, mode='w') as dest:
        for info in source:
            parts = PurePosixPath(info.name).parts
            if not parts or parts[0] != 'work' or '..' in parts or info.name.startswith('/') or '\\' in info.name:
                raise Fault(400, 'unsafe_artifact', 'Unsafe archive path')
            if len(parts) == 1:
                if not info.isdir():
                    raise Fault(400, 'unsafe_artifact', 'Invalid archive root')
                continue
            name = '/'.join(parts[1:])
            if name in seen or not (info.isfile() or info.isdir()):
                raise Fault(400, 'unsafe_artifact', 'Links and special files are not collected')
            seen.add(name)
            total += info.size
            if total > MAX_ARCHIVE or len(seen) > 10000:
                raise Fault(400, 'artifact_too_large', 'Artifact limit exceeded')
            clean = tarfile.TarInfo(name)
            clean.size = info.size
            clean.type = info.type
            clean.mode = 0o700 if info.isdir() else 0o600
            dest.addfile(clean, source.extractfile(info) if info.isfile() else None)
            if output.tell() > MAX_ARCHIVE:
                raise Fault(400, 'artifact_too_large', 'Artifact limit exceeded')
    if output.tell() > MAX_ARCHIVE:
        raise Fault(400, 'artifact_too_large', 'Artifact limit exceeded')
    return output.getvalue()


@dataclass
class Box:
    id: str
    container: str
    session: str
    mode: str
    lock: threading.RLock = field(default_factory=threading.RLock)
    operations: dict = field(default_factory=dict)
    active: str | None = None
    events: deque = field(default_factory=lambda: deque(maxlen=10000))
    seq: int = 0
    released: bool = False
    stopped: bool = False
    closed_at: float | None = None
    touched: float = field(default_factory=time.monotonic)
    artifacts: dict = field(default_factory=dict)


class Manager:
    def __init__(self, engine=None):
        self.engine = engine or Engine()
        self.epoch = uuid4().hex
        self.project = os.environ.get('SANDBOX_PROJECT', 'agent_harness')
        self.image = os.environ.get('SANDBOX_IMAGE', 'agent-harness-sandbox:cp1')
        self.memory = size(os.environ.get('SANDBOX_MEMORY', '256m'))
        self.disk = size(os.environ.get('SANDBOX_DISK', '64m'))
        self.cpu = int(float(os.environ.get('SANDBOX_CPU', '1')) * 1e9)
        self.pids = int(os.environ.get('SANDBOX_PIDS', '64'))
        self.max_exec = int(os.environ.get('SANDBOX_EXEC_TIMEOUT_S', '120'))
        if self.memory < 32*1024*1024 or self.disk < 1024*1024 or self.cpu <= 0 or self.pids < 8 or self.max_exec < 1:
            raise ValueError('Unsafe resource configuration')
        self.schema = json.loads(Path('/app/contracts/harness-sandbox.v0.schema.json').read_text())
        self.validator = Draft202012Validator(self.schema)
        self.lock = threading.RLock()
        self.boxes = {}
        self.sessions = {}
        self.requests = {}
        self.closing = False
        self.ready = False
        self.stop_event = threading.Event()
        self.token = ''

    def start(self):
        # Never become healthy while resources from a previous process remain.
        for item in self.engine.containers(self.project):
            self.engine.remove(item['Id'])
        self.engine.request('GET', f'/images/{self.image}/json')
        auth = Path('/run/runtime-auth/sandbox.token')
        auth.parent.mkdir(parents=True, exist_ok=True)
        if not auth.exists():
            auth.write_text(secrets.token_urlsafe(32))
            auth.chmod(0o644)  # Shared only with the harness through a read-only volume.
        self.token = auth.read_text().strip()
        if len(self.token) < 32:
            raise ValueError('Invalid service token file')
        self.ready = True
        threading.Thread(target=self.reap, daemon=True).start()

    def reap(self):
        while not self.stop_event.wait(5):
            with self.lock:
                boxes = list(self.boxes.values())
                now = time.time()
                self.requests = {key: item for key, item in self.requests.items() if item['expires'] > now}
            for box in boxes:
                with box.lock:
                    if not box.released and box.active is None and time.monotonic()-box.touched > 1800:
                        try:
                            self.release(box, 10)
                        except EngineError:
                            LOG.error('Idle sandbox cleanup failed')
                    if box.closed_at and time.monotonic()-box.closed_at > 3600:
                        with self.lock:
                            self.boxes.pop(box.id, None)
                            self.sessions.pop(box.session, None)

    def close(self):
        self.ready = False
        self.closing = True
        self.stop_event.set()
        # Remove by project label, including containers whose provision failed halfway.
        for item in self.engine.containers(self.project):
            self.engine.remove(item['Id'])

    def health(self):
        try:
            self.engine.request('GET', '/info', timeout=1)
            ok = self.ready and not self.closing
        except EngineError:
            ok = False
        return {'service': 'sandbox-manager', 'status': 'ready' if ok else 'not_ready',
                'stage': 'sandbox', 'dependencies': {'docker': 'ready' if ok else 'unavailable'}}

    def error(self, fault, request_id=None):
        return {'version': 'v0', 'type': 'error', 'request_id': request_id, 'server_epoch': self.epoch,
                'error': {'class': fault.category, 'code': fault.code, 'message': fault.message, 'retry': fault.retry}}

    def response(self, req, kind, **values):
        return {'version': 'v0', 'type': kind, 'request_id': req['request_id'], 'server_epoch': self.epoch, **values}

    def validate(self, req):
        if not isinstance(req, dict) or not self.validator.is_valid(req) or req.get('type') not in REQUESTS:
            raise Fault(400, 'invalid_request', 'Request does not match protocol v0')
        if req['type'] != 'connect' and req['server_epoch'] != self.epoch:
            raise Fault(409, 'stale_epoch', 'Server has restarted; do not replay old commands')
        if req['expires_at'] <= time.time():
            raise Fault(410, 'expired_request', 'Request expired')
        if req['expires_at'] > time.time() + 3600:
            raise Fault(400, 'invalid_request', 'Request window exceeds 3600 seconds')
        if self.closing:
            raise Fault(503, 'sandbox_unavailable', 'Server is stopping', 'sandbox_error', 'query_status')

    def handle(self, req):
        self.validate(req)
        if req['type'] == 'events.subscribe':
            raise Fault(400, 'invalid_request', 'Use /v0/events for subscriptions')
        fingerprint = json.dumps(req, sort_keys=True, separators=(',', ':'))
        with self.lock:
            previous = self.requests.get(req['request_id'])
            if previous:
                if previous['fingerprint'] != fingerprint:
                    raise Fault(409, 'idempotency_conflict', 'request_id reused with different payload')
                if previous['reply'] is None:
                    raise Fault(409, 'request_in_progress', 'Request is running', retry='same_request')
                return previous['reply']
            self.requests[req['request_id']] = {'fingerprint': fingerprint, 'expires': req['expires_at'], 'reply': None}
        try:
            reply = self.dispatch(req)
        except Fault as exc:
            reply = (exc.status, self.error(exc, req['request_id']))
        except (EngineError, OSError, tarfile.TarError) as exc:
            LOG.error('Sandbox operation failed: %s', type(exc).__name__)
            reply = (503, self.error(Fault(503, 'sandbox_unavailable', 'Sandbox operation failed', 'sandbox_error', 'query_status'), req['request_id']))
        except Exception:
            LOG.exception('Unexpected sandbox-manager error')
            reply = (503, self.error(Fault(503, 'sandbox_unavailable', 'Internal operation failure', 'sandbox_error', 'query_status'), req['request_id']))
        with self.lock:
            record = self.requests.get(req['request_id'])
            if record is not None:
                record['reply'] = reply
        return reply

    def get_box(self, box_id):
        with self.lock:
            box = self.boxes.get(box_id)
        if box is None:
            raise Fault(404, 'not_found', 'Unknown sandbox')
        return box

    def dispatch(self, req):
        kind = req['type']
        if kind == 'connect':
            return 200, self.response(req, 'connected', limits=LIMITS)
        if kind == 'provision':
            box = self.provision(req)
            return 200, self.response(req, 'provisioned', session_id=box.session, sandbox_id=box.id)
        box = self.get_box(req['sandbox_id'])
        deadline = time.monotonic() + req['timeout_s']
        if not box.lock.acquire(timeout=req['timeout_s']):
            raise Fault(504, 'timeout', 'Sandbox is busy', 'timeout', 'query_status')
        try:
            box.touched = time.monotonic()
            if kind == 'release':
                self.release(box, max(.05, deadline-time.monotonic()))
                return 200, self.response(req, 'released', sandbox_id=box.id)
            if kind in ('operation.get', 'cancel'):
                op = box.operations.get(req['operation_id'])
                if op is None:
                    raise Fault(404, 'not_found', 'Unknown operation')
                if kind == 'operation.get':
                    return 200, self.response(req, 'operation.state', sandbox_id=box.id, operation_id=req['operation_id'],
                                              state='terminal' if op['result'] else 'running', result=op['result'])
                terminal = op['result'] is not None
                if not terminal:
                    self.stop_operation(box, req['operation_id'], 'cancelled', req['timeout_s'])
                return 202, self.response(req, 'cancel.accepted', sandbox_id=box.id, operation_id=req['operation_id'],
                                          status='already_terminal' if terminal else 'cancelling')
            if kind == 'exec' and req['operation_id'] in box.operations:
                self.begin(box, req)
                return 202, self.response(req, 'exec.accepted', sandbox_id=box.id, operation_id=req['operation_id'])
            if box.released or box.stopped:
                raise Fault(409, 'sandbox_stopped', 'Sandbox was stopped; create a new session')
            if kind == 'exec':
                self.begin(box, req)
                return 202, self.response(req, 'exec.accepted', sandbox_id=box.id, operation_id=req['operation_id'])
            if kind == 'collect':
                if box.active:
                    raise Fault(409, 'conflict', 'An operation is running')
                # Docker's archive endpoint does not expose this tmpfs reliably.
                # Refuse collection while background user processes can modify files.
                processes = self.engine.request('GET', f'/containers/{box.container}/top',
                                                timeout=max(.05, deadline-time.monotonic()))['Processes']
                if len(processes) != 1:
                    raise Fault(409, 'background_processes', 'Stop background processes before collection')
                collected = bytearray()
                diagnostic = bytearray()
                def collect_output(stream, chunk):
                    target = collected if stream == 'stdout' else diagnostic
                    if len(target)+len(chunk) > (MAX_ARCHIVE if stream == 'stdout' else 4096):
                        raise Fault(400, 'artifact_too_large', 'Artifact limit exceeded')
                    target.extend(chunk)
                try:
                    code = self.engine.execute(box.container, ['python', '-I', '/opt/collect.py'], collect_output, deadline)
                except (TimeoutError, EngineError, Fault) as exc:
                    box.stopped = True
                    self.engine.kill(box.container)
                    if isinstance(exc, TimeoutError):
                        raise Fault(504, 'timeout', 'Collect deadline exceeded', 'timeout', 'query_status') from exc
                    raise
                if code:
                    reason = 'artifact_too_large' if b'artifact_too_large' in diagnostic else 'unsafe_artifact'
                    raise Fault(400, reason, 'Unable to collect a safe bounded archive')
                data = safe_archive(bytes(collected))
                if time.monotonic() > deadline:
                    raise Fault(504, 'timeout', 'Collect deadline exceeded', 'timeout', 'query_status')
                artifact_id = uuid4().hex
                # Bound retained snapshots per sandbox, not only each individual archive.
                if sum(len(v) for v in box.artifacts.values()) + len(data) > MAX_ARCHIVE:
                    raise Fault(409, 'artifact_storage_full', 'Release the session to clear artifact storage')
                box.artifacts[artifact_id] = data
                return 200, self.response(req, 'collected', sandbox_id=box.id, artifact_id=artifact_id,
                                          size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            raise Fault(400, 'invalid_request', 'Unsupported operation')
        finally:
            box.lock.release()

    def provision(self, req):
        workdir = req['workdir']
        sources = SOURCES
        if workdir['kind'] != 'fixture':
            raise Fault(400, 'unsupported_workdir_source', 'Remote mounting is not implemented')
        if workdir['source_id'] not in sources:
            raise Fault(400, 'unknown_fixture', 'Unknown fixture identifier')
        with self.lock:
            if req['session_id'] in self.sessions:
                raise Fault(409, 'conflict', 'Session already provisioned')
            pending = sum(value is None for value in self.sessions.values())
            if pending + sum(not b.released for b in self.boxes.values()) >= 8:
                raise Fault(409, 'capacity', 'Maximum eight sandboxes')
            self.sessions[req['session_id']] = None
        container = None
        end = time.monotonic()+req['timeout_s']
        remaining = lambda: max(.05, end-time.monotonic())
        box_id = uuid4().hex
        try:
            mode = workdir['mode']
            uid, permissions = (0, '700') if mode == 'ro' else (10001, '700')
            config = {'Image': self.image, 'User': '10001:10001', 'WorkingDir': '/work',
                      'Env': ['PATH=/usr/local/bin:/usr/bin:/bin', 'HOME=/tmp', 'PYTHONDONTWRITEBYTECODE=1', 'PYTHONUNBUFFERED=1'],
                      'Labels': {'agent_harness.owner': self.project, 'agent_harness.sandbox': box_id},
                      'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'CapDrop': ['ALL'],
                                     'SecurityOpt': ['no-new-privileges:true'], 'Memory': self.memory,
                                     'MemorySwap': self.memory, 'NanoCpus': self.cpu, 'PidsLimit': self.pids,
                                     'LogConfig': {'Type': 'none'},
                                     'Tmpfs': {'/work': f'rw,nosuid,nodev,size={self.disk},uid={uid},gid={uid},mode={permissions}',
                                               '/tmp': 'rw,nosuid,nodev,noexec,size=16777216,mode=1777'},
                                     'ShmSize': 1048576}}
            container = self.engine.request('POST', '/containers/create?name='+self.project+'-sandbox-'+box_id,
                                            config, timeout=remaining())['Id']
            self.engine.request('POST', f'/containers/{container}/start', timeout=remaining())
            archive = fixture_archive(sources[workdir['source_id']], mode)
            encoded = base64.b64encode(archive).decode()
            argv = ['python', '-I', '/opt/seed.py', mode] + [encoded[i:i+32000] for i in range(0, len(encoded), 32000)]
            code = self.engine.execute(container, argv, lambda stream, chunk: None, end,
                                       user='0:0' if mode == 'ro' else '10001:10001')
            if code != 0:
                raise Fault(503, 'fixture_failed', 'Fixture initialization failed', 'sandbox_error', 'query_status')
            if time.monotonic() > end:
                raise Fault(504, 'timeout', 'Provision deadline exceeded', 'timeout', 'query_status')
            box = Box(box_id, container, req['session_id'], mode)
            with self.lock:
                self.boxes[box.id] = box
                self.sessions[box.session] = box.id
            LOG.info(json.dumps({'event': 'provisioned', 'sandbox_id': box.id, 'session_id': box.session}))
            return box
        except Exception:
            try:
                if container:
                    self.engine.remove(container)
            finally:
                with self.lock:
                    self.sessions.pop(req['session_id'], None)
            raise

    def publish(self, box, kind, operation_id, **payload):
        box.seq += 1
        event = {'version': 'v0', 'type': kind, 'server_epoch': self.epoch, 'sandbox_id': box.id,
                 'operation_id': operation_id, 'event_id': box.seq, **payload}
        box.events.append((time.monotonic(), event))

    def finish(self, box, op_id, value):
        op = box.operations[op_id]
        if op['result'] is not None:
            return
        op['result'] = value
        op['timer'].cancel()
        box.active = None
        box.touched = time.monotonic()
        self.publish(box, 'exec.result', op_id, result=value)
        LOG.info(json.dumps({'event': 'exec.result', 'sandbox_id': box.id, 'operation_id': op_id, 'status': value['status']}))

    def begin(self, box, req):
        op_id = req['operation_id']
        fingerprint = (req['command'], req['timeout_s'])
        if op_id in box.operations:
            if box.operations[op_id]['fingerprint'] != fingerprint:
                raise Fault(409, 'idempotency_conflict', 'operation_id reused with different command')
            return
        if box.active:
            raise Fault(409, 'conflict', 'An operation is running')
        if len(box.operations) >= 1000:
            raise Fault(409, 'capacity', 'Session operation limit reached')
        command = req['command']
        if command['name'] == 'WriteFile' and box.mode == 'ro':
            raise Fault(400, 'read_only', 'Workdir is read-only')
        if command['name'] == 'WriteFile' and len(base64.b64decode(command['content_base64'], validate=True)) > 1024*1024:
            raise Fault(400, 'file_too_large', 'File limit is 1 MiB')
        budget = min(req['timeout_s'], self.max_exec)
        timer = threading.Timer(budget, self.expire, (box, op_id))
        timer.daemon = True
        op = {'fingerprint': fingerprint, 'result': None, 'timer': timer, 'stdout': '', 'stderr': '',
              'truncated': False, 'bytes': bytearray(), 'deadline': time.monotonic()+budget,
              'decoders': {name: codecs.getincrementaldecoder('utf-8')(errors='replace') for name in ('stdout', 'stderr')}}
        box.operations[op_id] = op
        box.active = op_id
        timer.start()
        threading.Thread(target=self.worker, args=(box, op_id, command), daemon=True).start()

    def stop_operation(self, box, op_id, status, timeout=10):
        op = box.operations[op_id]
        if op['result'] is not None:
            return
        box.stopped = True
        try:
            self.engine.kill(box.container, timeout=timeout)
            value = result(status, category='timeout' if status == 'timed_out' else 'protocol_error',
                           stdout=op['stdout'], stderr=op['stderr'], truncated=op['truncated'])
        except EngineError:
            value = result('failed', code='cleanup_failed', category='sandbox_error')
        self.finish(box, op_id, value)

    def expire(self, box, op_id):
        with box.lock:
            self.stop_operation(box, op_id, 'timed_out')

    def worker(self, box, op_id, command):
        op = box.operations[op_id]
        def emit(stream, chunk):
            with box.lock:
                if op['result'] is not None:
                    return
                if command['name'] == 'ReadFile' and stream == 'stdout':
                    if len(op['bytes'])+len(chunk) > 1398108:
                        raise EngineError(413)
                    op['bytes'].extend(chunk)
                    return
                text = op['decoders'][stream].decode(chunk, final=not chunk)
                available = max(0, 65536-len(op[stream]))
                accepted = text[:available]
                op['truncated'] |= len(text) > available
                op[stream] += accepted
                if accepted:
                    self.publish(box, 'exec.output', op_id, stream=stream, text=accepted)
        try:
            serialized = json.dumps(command, ensure_ascii=True)
            argv = ['python', '-I', '/opt/agent_tools.py'] + [serialized[i:i+32000] for i in range(0, len(serialized), 32000)]
            code = self.engine.execute(box.container, argv, emit, op['deadline'])
            with box.lock:
                if op['result'] is not None:
                    return
                if time.monotonic() >= op['deadline']:
                    self.stop_operation(box, op_id, 'timed_out')
                    return
                emit('stderr', b'')
                if command['name'] != 'ReadFile':
                    emit('stdout', b'')
                content = None
                if command['name'] == 'ReadFile' and code == 0:
                    content = op['bytes'].decode('ascii').strip()
                    base64.b64decode(content, validate=True)
                self.finish(box, op_id, result('success' if code == 0 else 'failed', code='nonzero_exit' if code else None,
                    stdout=op['stdout'], stderr=op['stderr'], exit_code=code if command['name'] == 'RunCommand' else None,
                    content=content, truncated=op['truncated']))
        except TimeoutError:
            with box.lock:
                self.stop_operation(box, op_id, 'timed_out')
        except Exception as exc:
            LOG.error('Exec transport failed: %s', type(exc).__name__)
            with box.lock:
                if op['result'] is None:
                    box.stopped = True
                    try:
                        self.engine.kill(box.container)
                    except EngineError:
                        pass
                    self.finish(box, op_id, result('failed', code='exec_failed', category='sandbox_error'))

    def release(self, box, timeout):
        if box.released:
            return
        self.engine.remove(box.container, timeout=timeout)
        box.stopped = box.released = True
        box.closed_at = time.monotonic()
        box.artifacts.clear()
        if box.active:
            self.finish(box, box.active, result('cancelled', category='protocol_error'))
        LOG.info(json.dumps({'event': 'released', 'sandbox_id': box.id}))

    def events(self, req, validate=True):
        if validate:
            self.validate(req)
        if req['type'] != 'events.subscribe':
            raise Fault(400, 'invalid_request', 'Expected events.subscribe')
        box = self.get_box(req['sandbox_id'])
        with box.lock:
            while box.events and time.monotonic()-box.events[0][0] > 3600:
                box.events.popleft()
            cursor = req['after_event_id']
            if cursor > box.seq:
                raise Fault(400, 'invalid_cursor', 'Cursor is in the future')
            oldest = box.events[0][1]['event_id'] if box.events else box.seq+1
            if cursor < oldest-1:
                raise Fault(410, 'cursor_expired', 'Event cursor expired')
            return [event for _, event in box.events if event['event_id'] > cursor]

    def artifact(self, identifier):
        with self.lock:
            boxes = list(self.boxes.values())
        for box in boxes:
            with box.lock:
                if identifier in box.artifacts:
                    return box.artifacts[identifier]
        raise Fault(404, 'not_found', 'Unknown artifact')
