import base64
import importlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from urllib.error import HTTPError
from services.common.http_client import RemoteError, request_json
from services.common.pairs import load_pairs
from services.common.tools import command,observation

proxy=importlib.import_module('services.llm-proxy.runtime.proxy')
ROOT=Path(__file__).resolve().parents[2]


class ModelAdapterTests(unittest.TestCase):
    def test_write_tool_encodes_utf8_and_rejects_extra_fields(self):
        value=command('WriteFile',{'path':'text','content':'Привет\r\n'})
        self.assertEqual(base64.b64decode(value['content_base64']),'Привет\r\n'.encode())
        for name,args in [('Shell',{}),('ReadFile',{'path':'../x'}),('ReadFile',{'path':'x','env':{}})]:
            with self.assertRaises(ValueError):command(name,args)

    def test_observation_truncates_without_corrupting_json(self):
        value={'status':'success','stdout':'x'*10000,'stderr':'','exit_code':0,'error':None,
               'content_base64':None,'truncated':False}
        out=observation(value)
        self.assertEqual(len(out['stdout']),8192)
        self.assertTrue(out['truncated'])
        self.assertEqual(json.loads(json.dumps(out)),out)

    def test_normalization_usage_and_tool_calls(self):
        message={'role':'assistant','content':None,'tool_calls':[{'id':'call1','type':'function',
                    'function':{'name':'ReadFile','arguments':'{"path":"x"}'}}]}
        body={'model':'test-model','choices':[{'message':message,'finish_reason':'tool_calls'}],
              'usage':{'prompt_tokens':10,'completion_tokens':5}}
        value=proxy.normalize(body,'fallback')
        self.assertEqual(value['usage'],{'tokens_in':10,'tokens_out':5})
        self.assertEqual(value['message'],message)
        del body['usage']
        self.assertIsNone(proxy.normalize(body,'fallback')['usage']['tokens_in'])

    def test_rejects_malformed_or_truncated_model_response(self):
        for body in ({'choices':[]},{'choices':[{'message':{'role':'assistant','content':None}}]},
                     {'choices':[{'message':{'role':'assistant','content':'partial'},'finish_reason':'length'}]}):
            with self.assertRaises(proxy.ModelError):proxy.normalize(body,'test')

    def test_history_requires_matching_tool_results(self):
        base=[{'role':'system','content':'s'},{'role':'user','content':'u'}]
        bad=base+[{'role':'tool','tool_call_id':'unknown','content':'{}'}]
        with self.assertRaises(proxy.ModelError):proxy.validate_history(bad)
        missing=base+[{'role':'assistant','content':None,'tool_calls':[{'id':'pending'}]}]
        with self.assertRaises(proxy.ModelError):proxy.validate_history(missing)

    def test_provider_configuration_and_secret_boundary(self):
        pairs=load_pairs(ROOT/'configs/pairs')
        with patch.dict(os.environ,{'LLM_MODE':'real','LLM_PROVIDER_URL':'https://api.openai.com/v1',
                                    'LLM_MODEL':'test-model','LLM_API_KEY':'CANARY_PROVIDER_SECRET'}):
            adapter=proxy.Proxy(pairs,ROOT/'tests/fixtures/llm')
        request={'pair_id':'first-real-pair','timeout_s':10,'messages':[{'role':'system','content':'s'},{'role':'user','content':'u'}]}
        with patch.object(proxy,'request_json',return_value={'model':'test-model','choices':[{'message':{'role':'assistant','content':'done'}}]}) as remote:
            value=adapter.complete(request)
            args,kwargs=remote.call_args
            self.assertEqual(args[0],'https://api.openai.com/v1/chat/completions')
            self.assertEqual(args[1]['reasoning_effort'],'none')
            self.assertEqual(args[1]['max_completion_tokens'],2048)
            self.assertEqual(kwargs['token'],'CANARY_PROVIDER_SECRET')
            self.assertNotIn('CANARY_PROVIDER_SECRET',json.dumps(value))
        with patch.object(proxy,'request_json',side_effect=RemoteError('CANARY_PROVIDER_SECRET',status=401)):
            with self.assertRaises(proxy.ModelError) as failure:adapter.complete(request)
            self.assertEqual(failure.exception.code,'provider_auth_error')
            self.assertNotIn('CANARY_PROVIDER_SECRET',str(failure.exception))
        with patch.object(proxy,'request_json',side_effect=RemoteError('http_429',status=429,provider_code='insufficient_quota')):
            with self.assertRaises(proxy.ModelError) as failure:adapter.complete(request)
            self.assertEqual(failure.exception.code,'provider_insufficient_quota')

    def test_credit_balance_error_is_normalized_without_message(self):
        bodies = [
            {'code':'credit_balance_exhausted','type':'insufficient_quota'},
            {'code':None,'type':'insufficient_quota'},
            {'code':'rate_limit_exceeded','type':'requests'},
        ]
        for body in bodies:
            body['message']='CANARY_PRIVATE_MESSAGE'
            error=HTTPError('https://api.openai.com/v1/chat/completions',429,'error',{},
                            io.BytesIO(json.dumps({'error':body}).encode()))
            with patch('services.common.http_client.build_opener') as opener:
                opener.return_value.open.side_effect=error
                with self.assertRaises(RemoteError) as failure:
                    request_json('https://api.openai.com/v1/chat/completions',{})
            expected='rate_limit_exceeded' if body['type']=='requests' else 'insufficient_quota'
            self.assertEqual(failure.exception.provider_code,expected)
            self.assertNotIn('CANARY',str(failure.exception))

    def test_replay_refuses_wrong_observation(self):
        pairs=load_pairs(ROOT/'configs/pairs')
        adapter=proxy.Proxy(pairs,ROOT/'tests/fixtures/llm')
        messages=[{'role':'system','content':'s'},{'role':'user','content':'порт'}]
        first=adapter.complete({'pair_id':'replay-b1','messages':messages,'timeout_s':10})
        messages.extend([first['message'],{'role':'tool','tool_call_id':'read_config','content':json.dumps({'status':'success','text':'wrong'})}])
        with self.assertRaises(proxy.ModelError) as failure:
            adapter.complete({'pair_id':'replay-b1','messages':messages,'timeout_s':10})
        self.assertEqual(failure.exception.code,'replay_observation_mismatch')


if __name__=='__main__':
    unittest.main()
