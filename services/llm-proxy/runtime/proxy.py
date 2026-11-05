"""Replay and Chat Completions adapters. Only this process sees the provider key."""
import copy
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from services.common.http_client import RemoteError, request_json
from services.common.tools import definitions


class ModelError(Exception):
    def __init__(self, code, status=502):
        self.code, self.status = code, status
        super().__init__(code)


def normalize(body, fallback_model):
    try:
        choice = body['choices'][0]
        raw = choice['message']
        if raw['role'] != 'assistant':
            raise ValueError()
        message = {'role':'assistant','content':raw.get('content')}
        if message['content'] is not None and not isinstance(message['content'],str):
            raise ValueError()
        if len(message['content'] or '') > 65536:
            raise ValueError()
        calls = raw.get('tool_calls') or []
        if not isinstance(calls,list) or len(calls)>8:
            raise ValueError()
        ids = set()
        clean = []
        for call in calls:
            identifier = call['id']
            function = call['function']
            if not isinstance(identifier,str) or not identifier or len(identifier)>128 or identifier in ids:
                raise ValueError()
            if call['type']!='function' or not isinstance(function['name'],str) or len(function['name'])>80:
                raise ValueError()
            if not isinstance(function['arguments'],str) or len(function['arguments'])>65536:
                raise ValueError()
            ids.add(identifier)
            clean.append({'id':identifier,'type':'function','function':{'name':function['name'],'arguments':function['arguments']}})
        if clean:
            message['tool_calls'] = clean
        if isinstance(raw.get('reasoning_content'),str):
            # Preserve provider-specific reasoning state for the next tool round; never log it.
            if len(raw['reasoning_content'])>131072:
                raise ValueError()
            message['reasoning_content'] = raw['reasoning_content']
        if not clean and not message['content']:
            raise ValueError()
        finish = choice.get('finish_reason')
        if finish in ('length','content_filter'):
            raise ModelError('model_'+finish)
        usage = body.get('usage') or {}
        tokens = {}
        for key, provider_key in [('tokens_in','prompt_tokens'),('tokens_out','completion_tokens')]:
            value = usage.get(provider_key)
            tokens[key] = value if type(value) is int and value>=0 else None
        model = body.get('model') or fallback_model
        if not isinstance(model,str) or not model:
            raise ValueError()
        return {'message':message,'usage':tokens,'model':model}
    except (KeyError, IndexError, TypeError, ValueError):
        raise ModelError('malformed_model_response') from None


def validate_history(messages):
    if not isinstance(messages,list) or len(messages)<2 or len(messages)>300:
        raise ModelError('invalid_history',400)
    if messages[0].get('role')!='system' or messages[1].get('role')!='user':
        raise ModelError('invalid_history',400)
    pending = set()
    for index, item in enumerate(messages):
        if not isinstance(item,dict):
            raise ModelError('invalid_history',400)
        role = item.get('role')
        if index<2:
            if not isinstance(item.get('content'),str):
                raise ModelError('invalid_history',400)
            continue
        if role=='assistant':
            if pending:
                raise ModelError('missing_tool_results',400)
            calls=item.get('tool_calls',[])
            ids=[call.get('id') for call in calls]
            if not all(isinstance(x,str) and x for x in ids) or len(set(ids))!=len(ids):
                raise ModelError('invalid_tool_ids',400)
            pending=set(ids)
        elif role=='user':
            if pending or not isinstance(item.get('content'),str):
                raise ModelError('invalid_history',400)
        elif role=='tool':
            if item.get('tool_call_id') not in pending or not isinstance(item.get('content'),str):
                raise ModelError('orphan_tool_result',400)
            pending.remove(item['tool_call_id'])
        else:
            raise ModelError('invalid_history',400)
    if pending:
        raise ModelError('missing_tool_results',400)


class Proxy:
    def __init__(self, pairs, fixture_dir='/app/replays'):
        self.pairs=pairs
        self.fixture_dir=Path(fixture_dir)
        self.mode=os.environ.get('LLM_MODE','replay')
        self.url=os.environ.get('LLM_PROVIDER_URL','').rstrip('/')
        self.model=os.environ.get('LLM_MODEL','')
        self.key=os.environ.get('LLM_API_KEY','')

    def complete(self, payload):
        if not isinstance(payload,dict) or set(payload)!={'pair_id','messages','timeout_s'}:
            raise ModelError('invalid_request',400)
        pair=self.pairs.get(payload['pair_id'])
        if pair is None:
            raise ModelError('unknown_pair',400)
        if type(payload['timeout_s']) not in (int,float) or not 0<payload['timeout_s']<=30:
            raise ModelError('invalid_timeout',400)
        messages=payload['messages']
        if len(json.dumps(messages))>262144:
            raise ModelError('context_limit',400)
        validate_history(messages)
        if pair['mode']=='replay':
            value=self.replay(pair,messages)
            return {**value,'mode':'replay'}
        if self.mode!='real' or not self.url or not self.model or not self.key:
            raise ModelError('provider_not_configured',503)
        parsed=urlsplit(self.url)
        if parsed.scheme!='https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ModelError('invalid_provider_url',503)
        model=self.model if pair['model']=='$LLM_MODEL' else pair['model']
        request={'model':model,'messages':messages,'tools':definitions(),'stream':False,**pair['parameters']}
        api=pair.get('api','chat')
        endpoint='/chat/completions'
        if api=='responses':
            from .responses import payload as responses_payload
            request=responses_payload(model,messages,pair['parameters'])
            endpoint='/responses'
        try:
            body=request_json(self.url+endpoint,request,token=self.key,
                              timeout=payload['timeout_s'])
        except RemoteError as exc:
            # No provider error body/URL/key crosses this boundary. No automatic billable retries.
            code={401:'provider_auth_error',403:'provider_forbidden',404:'provider_model_not_found',429:'provider_rate_limit',400:'provider_bad_request'}.get(exc.status,'provider_timeout' if exc.code=='transport_failure' else 'provider_error')
            if exc.provider_code=='insufficient_quota':
                code='provider_insufficient_quota'
            raise ModelError(code,503) from None
        if api=='responses':
            from .responses import response as responses_response
            return {**responses_response(body,model),'mode':'real'}
        return {**normalize(body,model),'mode':'real'}

    def replay(self,pair,messages):
        scenario=json.loads((self.fixture_dir/(pair['replay']+'.json')).read_text())
        if 'routes' in scenario:
            matches=sorted([(len(key),value) for key,value in scenario['routes'].items() if key in messages[1]['content'].lower()],key=lambda x:x[0],reverse=True)
            if not matches or (len(matches)>1 and matches[0][0]==matches[1][0]): raise ModelError('replay_prompt_mismatch',409)
            scenario=matches[0][1]
        if scenario.get('prompt_contains') and scenario['prompt_contains'] not in messages[1]['content'].lower():
            raise ModelError('replay_prompt_mismatch',409)
        last_user=max(i for i,item in enumerate(messages) if item['role']=='user')
        if last_user>1 and 'followup' in scenario:
            scenario=scenario['followup']
            if scenario.get('history_contains','') not in json.dumps(messages[:last_user]):
                raise ModelError('replay_history_mismatch',409)
        step=sum(item['role']=='assistant' for item in messages[last_user+1:])
        if scenario.get('repeat'):
            turn=copy.deepcopy(scenario['turns'][0])
            turn['reply']['tool_calls'][0]['id']='loop_'+str(step)
        elif step>=len(scenario['turns']):
            raise ModelError('replay_exhausted',409)
        else:
            turn=scenario['turns'][step]
        expected=turn.get('expect')
        if expected:
            last=messages[-1]
            if last.get('role')!='tool' or last.get('tool_call_id')!=expected['call_id']:
                raise ModelError('replay_history_mismatch',409)
            try:
                value=json.loads(last['content'])
                for key in ('status','exit_code','stdout'):
                    if key in expected and value.get(key)!=expected[key]:
                        raise ValueError()
                if 'text_contains' in expected and expected['text_contains'] not in value.get('text',''):
                    raise ValueError()
                if 'error_code' in expected and value.get('error',{}).get('code')!=expected['error_code']:
                    raise ValueError()
            except (ValueError,TypeError,AttributeError):
                raise ModelError('replay_observation_mismatch',409) from None
        return normalize({'model':pair['model'],'choices':[{'message':turn['reply'],'finish_reason':'stop'}]},pair['model'])
