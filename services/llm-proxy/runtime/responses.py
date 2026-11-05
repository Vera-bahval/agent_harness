"""Stateless Responses adapter; preserve encrypted reasoning only in the private run history."""
import copy
from .proxy import ModelError, normalize
from services.common.tools import definitions


def payload(model,messages,parameters):
    inputs=[]
    for message in messages:
        if message['role']=='assistant' and 'response_items' in message:
            inputs.extend(copy.deepcopy(message['response_items']))
        elif message['role']=='tool':
            inputs.append({'type':'function_call_output','call_id':message['tool_call_id'],'output':message['content']})
        else:
            inputs.append({'role':'developer' if message['role']=='system' else message['role'],'content':message.get('content') or ''})
    return {'model':model,'input':inputs,'store':False,'stream':False,
            'reasoning':{'effort':parameters['reasoning_effort']},
            'max_output_tokens':parameters['max_completion_tokens'],
            'parallel_tool_calls':parameters.get('parallel_tool_calls',False),
            'tools':[{'type':'function',**tool['function'],'strict':False} for tool in definitions()]}


def response(body,model):
    try:
        if body.get('status')!='completed': raise ModelError('model_incomplete')
        items=body['output']
        if not isinstance(items,list) or len(items)>40: raise ValueError()
        calls=[];text=[]
        for item in items:
            if item['type']=='function_call':
                calls.append({'id':item['call_id'],'type':'function','function':{'name':item['name'],'arguments':item['arguments']}})
            elif item['type']=='message':
                if item.get('role')!='assistant': raise ValueError()
                for content in item['content']:
                    if content['type']=='output_text': text.append(content['text'])
                    else: raise ModelError('model_refusal')
            elif item['type']!='reasoning': raise ValueError()
        usage=body.get('usage') or {}
        normalized=normalize({'model':body.get('model',model),'choices':[{'message':{'role':'assistant','content':'\n'.join(text) or None,'tool_calls':calls},'finish_reason':'stop'}],
                              'usage':{'prompt_tokens':usage.get('input_tokens'),'completion_tokens':usage.get('output_tokens')}},model)
        normalized['message']['response_items']=copy.deepcopy(items)
        return normalized
    except (KeyError,TypeError,ValueError): raise ModelError('malformed_model_response') from None
