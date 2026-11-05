import json
from pathlib import Path
import sys
from jsonschema import Draft202012Validator

# Hidden checker schema, never copied into the agent fixture.
SCHEMA={'type':'object','additionalProperties':False,
        'required':['name','port','debug','retries','environments'],
        'properties':{'name':{'type':'string','const':'agent-service'},
                      'port':{'type':'integer','const':8085},
                      'debug':{'type':'boolean','const':False},
                      'retries':{'type':'integer','const':3},
                      'environments':{'type':'array','const':['dev','test']}}}
def unique(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError('duplicate key')
        result[key]=value
    return result
try:
    value=json.loads((Path(sys.argv[1])/'config.json').read_text(),object_pairs_hook=unique)
    ok=Draft202012Validator(SCHEMA).is_valid(value)
    reason='ok' if ok else 'config.json does not match specification'
except (OSError,ValueError,UnicodeError):
    ok=False;reason='config.json missing or invalid JSON'
print(json.dumps({'pass':ok,'reason':reason}))
