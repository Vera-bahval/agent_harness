"""Server-owned pair registry; callers select IDs, not arbitrary providers/config paths."""
import json
from pathlib import Path


def load_pairs(directory='/app/configs/pairs'):
    pairs = {}
    for path in Path(directory).glob('*.json'):
        item = json.loads(path.read_text())
        required = {'id','harness','mode','model','max_steps','timeout_s','tool_timeout_s','parameters','replay'}
        if set(item)-{'api'} != required or item.get('api','chat') not in ('chat','responses') or item['harness'] != 'simple-v0' or item['mode'] not in ('replay','real'):
            raise ValueError('Invalid pair configuration')
        for field, upper in [('max_steps',100),('timeout_s',1800),('tool_timeout_s',120)]:
            if type(item[field]) is not int or not 1 <= item[field] <= upper:
                raise ValueError('Invalid pair budget')
        if not isinstance(item['parameters'],dict) or set(item['parameters'])-{'temperature','max_tokens','max_completion_tokens','reasoning_effort','parallel_tool_calls','thinking'}:
            raise ValueError('Unsupported model parameters')
        if item['id'] != path.stem or item['id'] in pairs:
            raise ValueError('Invalid pair ID')
        if item['mode']=='replay' and (not isinstance(item['replay'],str) or not item['replay'].replace('_','').isalnum()):
            raise ValueError('Invalid replay ID')
        pairs[item['id']] = item
    if not pairs:
        raise ValueError('Pair registry is empty')
    return pairs
