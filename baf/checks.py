"""Trusted CP1 checkers. This file and checker wrappers stay outside agent workdirs."""
import ast
import csv
import io
import json
from pathlib import Path

import yaml

READERS={f'pkg/module_{n:03d}.py' for n in (3,17,29,46,58,71,92,113,128,145,167,193)}
REPORT=[['date','customer','amount'],['2025-12-31','Dan','42.00'],['2026-01-03','Bob','9.00'],['2026-10-01','Carol','75.25'],['2026-11-12','Alice','120.50']]


class UniqueLoader(yaml.SafeLoader): pass

def mapping(loader,node,deep=False):
    result={}
    for key,value in node.value:
        key=loader.construct_object(key,deep=deep)
        if key in result: raise ValueError('duplicate_yaml_key')
        result[key]=loader.construct_object(value,deep=deep)
    return result
UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,mapping)


def unchanged(work,fixture):
    return all((work/p.relative_to(fixture)).read_bytes()==p.read_bytes() for p in fixture.rglob('*') if p.is_file())


def verify(identifier,work,fixture):
    if identifier=='B1.2': return (work/'answer.txt').read_text().strip()=='37'
    if identifier=='B1.3': return (work/'answer.txt').read_text().strip()=='НЕТ ДАННЫХ'
    if identifier=='B2.2':
        paths=(work/'answer.txt').read_text().splitlines()
        return len(paths)==len(set(paths)) and set(paths)==READERS
    if identifier=='B2.3': return (work/'answer.txt').read_text().strip()=='api/timeparse.py'
    if identifier=='B3.2': return list(csv.reader(io.StringIO((work/'report.csv').read_text())))==REPORT
    if identifier=='B3.3':
        for name,expected in [('countries.yaml',{'norway':'NO','ontario':'ON','disabled':'no','enabled':'on'}),('postal.yaml',{'home':'0123','office':'0010'})]:
            actual=yaml.load((work/name).read_text(),Loader=UniqueLoader)
            if actual!=expected or not all(type(v) is str for v in actual.values()): return False
        return True
    if identifier=='B4.2':
        # Validate the entire AST against a trusted rename before running the bounded hidden cases.
        # Arbitrary agent code is rejected before compile/exec; no imports are evaluated here.
        for source in fixture.rglob('*'):
            if not source.is_file(): continue
            actual=(work/source.relative_to(fixture)).read_text()
            expected=source.read_text().replace('calc_total','compute_total')
            if 'calc_total' in actual: return False
            if source.suffix=='.py':
                if ast.dump(ast.parse(actual))!=ast.dump(ast.parse(expected)): return False
            elif actual!=expected: return False
        tree=ast.parse((work/'pricing.py').read_text())
        namespace={'__builtins__':{'sum':sum}}
        exec(compile(tree,'verified_pricing','exec'),namespace)
        compute=namespace['compute_total']
        return all(compute(items)==expected for items,expected in [([],0),([(5,2),(3,4)],22),([(1.25,4)],5),([(-2,3),(9,0)],-6)])
    if identifier=='B4.3':
        expected=(fixture/'prices.csv').read_bytes().replace('X;Кофе;850\r\n'.encode('cp1251'),'X;Кофе;990\r\n'.encode('cp1251'))
        return (work/'prices.csv').read_bytes()==expected
    if identifier=='B5.2':
        names=(work/'failed.txt').read_text().splitlines()
        return len(names)==3 and set(names)=={'test_03','test_07','test_14'} and unchanged(work,fixture)
    if identifier=='B5.3':
        lines=(work/'answer.txt').read_text().strip().splitlines()
        return len(lines)>=2 and lines[0].strip()=='no' and 'missing required environment variable RELEASE_TOKEN' in '\n'.join(lines[1:]) and unchanged(work,fixture)
    raise ValueError('unknown_checker')


def main(identifier,work,fixture):
    try:
        passed=bool(verify(identifier,Path(work),Path(fixture)))
        reason='ok' if passed else 'answer_or_project_does_not_match_task'
    except (OSError,ValueError,UnicodeError,TypeError,KeyError,SyntaxError,yaml.YAMLError):
        passed=False;reason='missing_or_invalid_result'
    print(json.dumps({'pass':passed,'reason':reason},ensure_ascii=False))
