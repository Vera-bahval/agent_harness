"""All user workdir operations cross this HTTP boundary."""
import json
import math
import os
from pathlib import Path
import time
from uuid import uuid4
from urllib.request import ProxyHandler, Request, build_opener

from jsonschema import Draft202012Validator
from services.common.http_client import RemoteError, request_json


class Cancelled(Exception):
    pass


class Deadline(Exception):
    pass


class SandboxClient:
    def __init__(self):
        self.url=os.environ.get('SANDBOX_URL','http://sandbox-manager:8000').rstrip('/')
        self.token=Path(os.environ.get('SANDBOX_TOKEN_FILE','/run/runtime-auth/sandbox.token')).read_text().strip()
        self.validator=Draft202012Validator(json.loads(Path('/app/contracts/harness-sandbox.v0.schema.json').read_text()))
        self.epoch=None
        self.epoch=self.call('connect',timeout=5)['server_epoch']

    def call(self,kind,timeout=10,**fields):
        req={'version':'v0','type':kind,'request_id':uuid4().hex,'expires_at':int(time.time())+300,
             'timeout_s':max(1,min(1800,math.ceil(timeout))),**fields}
        if self.epoch:
            req['server_epoch']=self.epoch
        response=request_json(self.url+'/v0/requests',req,timeout=timeout+2,token=self.token,trusted_errors=True)
        if not self.validator.is_valid(response):
            raise RemoteError('invalid_sandbox_response','protocol_error')
        if self.epoch is not None and response['server_epoch']!=self.epoch:
            raise RemoteError('stale_epoch','sandbox_error')
        return response

    def execute(self,box,operation,command,deadline,cancel,tool_timeout):
        budget=max(1,min(tool_timeout,math.ceil(deadline-time.monotonic())))
        # No blind retries: an unknown outcome terminates this run and release cleans up.
        self.call('exec',timeout=min(budget,10),timeout_s=budget,sandbox_id=box,
                  operation_id=operation,command=command)
        while True:
            if cancel.is_set():
                self.call('cancel',timeout=10,sandbox_id=box,operation_id=operation)
                raise Cancelled()
            if time.monotonic()>=deadline:
                self.call('cancel',timeout=10,sandbox_id=box,operation_id=operation)
                raise Deadline()
            value=self.call('operation.get',timeout=min(5,max(.1,deadline-time.monotonic())),
                            sandbox_id=box,operation_id=operation)
            if value['state']=='terminal':
                return value['result']
            cancel.wait(.05)

    def artifact(self,box,deadline):
        remaining=max(.1,deadline-time.monotonic())
        metadata=self.call('collect',timeout=min(30,remaining),sandbox_id=box)
        request=Request(self.url+'/v0/artifacts/'+metadata['artifact_id'],headers={'Authorization':'Bearer '+self.token})
        with build_opener(ProxyHandler({})).open(request,timeout=min(30,max(.1,deadline-time.monotonic()))) as response:
            data=response.read(64*1024*1024+1)
        import hashlib
        if len(data)!=metadata['size_bytes'] or hashlib.sha256(data).hexdigest()!=metadata['sha256']:
            raise RemoteError('invalid_artifact','sandbox_error')
        return data
