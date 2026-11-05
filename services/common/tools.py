"""Model-facing tool interface. Sandbox wire format remains in contracts/."""
import base64
import copy
from jsonschema import Draft202012Validator

PARAMETERS = {
    'RunCommand': {'type':'object','properties':{
        'argv':{'type':'array','minItems':1,'maxItems':128,'items':{'type':'string','maxLength':8192},
                'prefixItems':[{'type':'string','minLength':1,'maxLength':8192}]},
        'cwd':{'type':'string','minLength':1,'maxLength':4096}},
        'required':['argv','cwd'],'additionalProperties':False},
    'ReadFile': {'type':'object','properties':{'path':{'type':'string','minLength':1,'maxLength':4096}},
                 'required':['path'],'additionalProperties':False},
    'WriteFile': {'type':'object','properties':{'path':{'type':'string','minLength':1,'maxLength':4096},
                                              'content':{'type':'string','maxLength':65536}},
                  'required':['path','content'],'additionalProperties':False},
}
DESCRIPTIONS = {
    'RunCommand':'Run argv in the sandbox. cwd is relative to /work; use . for its root. Shell must be explicit.',
    'ReadFile':'Read a file relative to /work. Returns text (UTF-8), or base64 for binary files, and a truncation flag.',
    'WriteFile':'Create or replace a UTF-8 text file relative to /work. Parent directory must exist. For binary edits use RunCommand.',
}


def definitions():
    return [{'type':'function','function':{'name':name,'description':DESCRIPTIONS[name],
            'parameters':copy.deepcopy(schema)}} for name,schema in PARAMETERS.items()]


def command(name, arguments):
    if name not in PARAMETERS or not Draft202012Validator(PARAMETERS[name]).is_valid(arguments):
        raise ValueError('invalid_tool_arguments')
    path = arguments.get('path', arguments.get('cwd'))
    if path.startswith('/') or '..' in path.split('/') or '\\' in path or '\x00' in path:
        raise ValueError('invalid_tool_path')
    if name == 'WriteFile':
        return {'name':name,'path':arguments['path'],
                'content_base64':base64.b64encode(arguments['content'].encode()).decode()}
    return {'name':name, **arguments}


def observation(value, limit=8192):
    result = {key:value[key] for key in ('status','stdout','stderr','exit_code','error','truncated')}
    for key in ('stdout','stderr'):
        if len(result[key]) > limit:
            result[key] = result[key][:limit]
            result['truncated'] = True
    if value['content_base64'] is not None:
        raw = base64.b64decode(value['content_base64'], validate=True)
        try:
            text = raw.decode('utf-8')
            result['text'] = text[:limit]
            result['encoding'] = 'utf-8'
            result['truncated'] |= len(text) > limit
        except UnicodeDecodeError:
            result['content_base64'] = value['content_base64'][:limit]
            result['encoding'] = 'base64'
            result['truncated'] |= len(value['content_base64']) > limit
    return result
