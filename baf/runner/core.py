"""Task discovery, safe artifact handling and trusted checker execution."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import subprocess
import sys
import tarfile
import tempfile
import time

import yaml
from client.cli import ClientError

MAX_BYTES=64*1024*1024


@dataclass
class Task:
    id: str
    directory: Path
    messages: list
    timeout: int
    source_id: str
    metadata: dict


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader,node,deep=False):
    result={}
    for key,value in node.value:
        key=loader.construct_object(key,deep=deep)
        if not isinstance(key,str) or key in result: raise ValueError('duplicate_or_invalid_yaml_key')
        result[key]=loader.construct_object(value,deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,unique_mapping)


def discover(root):
    root=Path(root)
    registry=json.loads((root/'fixtures.json').read_text())
    tasks={}
    for path in sorted(root.rglob('task.yaml')):
        if 'fixture' in path.relative_to(root).parts: continue
        if path.stat().st_size>65536: raise ValueError('task_too_large')
        data=yaml.load(path.read_text(),Loader=UniqueLoader)
        required={'id','baf','level','messages','answer_file','timeout_s'}
        if not isinstance(data,dict) or set(data)!=required: raise ValueError('invalid_task_fields')
        identifier=data['id']
        if not isinstance(identifier,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}',identifier): raise ValueError('invalid_task_id')
        if identifier in tasks: raise ValueError('duplicate_task_id')
        if data['level'] not in ('simple','medium','tricky'): raise ValueError('invalid_level')
        if not isinstance(data['baf'],str) or not data['baf']: raise ValueError('invalid_baf')
        if type(data['timeout_s']) is not int or not 1<=data['timeout_s']<=1800: raise ValueError('invalid_task_timeout')
        messages=data['messages']
        if not isinstance(messages,list) or not 1<=len(messages)<=20 or any(not isinstance(m,str) or not 0<len(m)<=65536 for m in messages): raise ValueError('invalid_messages')
        safe_name(data['answer_file'])
        source=registry.get(identifier)
        if not isinstance(source,str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}',source): raise ValueError('unregistered_fixture')
        folder=path.parent
        if not (folder/'fixture').is_dir() or not (folder/'check.py').is_file() or (folder/'check.py').is_symlink(): raise ValueError('missing_fixture_or_checker')
        tree_digest(folder/'fixture')  # reject links/special files before any paid run
        tasks[identifier]=Task(identifier,folder,messages,data['timeout_s'],source,data)
    if not tasks: raise ValueError('no_tasks')
    return tasks


def safe_name(name):
    if not isinstance(name,str) or not name or '\\' in name or '\0' in name: raise ValueError('unsafe_path')
    path=PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or not path.parts: raise ValueError('unsafe_path')
    return path


def tree_digest(root):
    entries=[]
    total=0
    for path in sorted(Path(root).rglob('*')):
        relative=path.relative_to(root).as_posix()
        if path.is_symlink() or not (path.is_dir() or path.is_file()): raise ValueError('unsafe_fixture')
        total+=path.stat().st_size if path.is_file() else 0
        if total>MAX_BYTES or len(entries)>=10000: raise ValueError('fixture_too_large')
        entries.append([relative,'dir' if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()])
    return hashlib.sha256(json.dumps(entries,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def unpack(archive_path,target):
    """Do not extract links, special files, duplicates, traversal or unbounded archives."""
    target=Path(target)
    if target.exists() and any(target.iterdir()): raise ValueError('nonempty_extract_target')
    target.mkdir(parents=True,exist_ok=True)
    seen=set();total=0
    with tarfile.open(archive_path,'r:') as archive:
        for member in archive:
            if member.name in ('.','./') and member.isdir(): continue
            relative=safe_name(member.name)
            name=str(relative)
            if name in seen or len(seen)>=10000 or not (member.isfile() or member.isdir()): raise ValueError('unsafe_archive')
            seen.add(name)
            total+=member.size
            if member.size<0 or total>MAX_BYTES: raise ValueError('archive_too_large')
            destination=target.joinpath(*relative.parts)
            if member.isdir(): destination.mkdir(parents=True,exist_ok=True)
            else:
                destination.parent.mkdir(parents=True,exist_ok=True)
                with archive.extractfile(member) as source, destination.open('xb') as output:
                    remaining=member.size
                    while remaining:
                        chunk=source.read(min(65536,remaining))
                        if not chunk: raise ValueError('truncated_archive')
                        output.write(chunk);remaining-=len(chunk)


def check(checker,workdir,timeout=10):
    """Execute only the repository-owned checker; strip credentials and kill its process group."""
    import resource
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE,(1024*1024,1024*1024))
        resource.setrlimit(resource.RLIMIT_CPU,(max(1,int(timeout)+1),max(1,int(timeout)+1)))
        resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
    with tempfile.TemporaryDirectory(prefix='baf-checker-') as cwd, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process=subprocess.Popen([sys.executable,'-I',str(Path(checker).resolve()),str(Path(workdir).resolve())],
            cwd=cwd,env={'PATH':os.defpath,'LANG':'C.UTF-8'},stdin=subprocess.DEVNULL,stdout=out,stderr=err,
            start_new_session=True,preexec_fn=limits)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return {'pass':False,'reason':'checker_timeout','outcome':'checker_timeout'}
        finally:
            try: os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait()
        if process.returncode!=0: return {'pass':False,'reason':'checker_nonzero_exit','outcome':'checker_error'}
        out.seek(0)
        try:
            value=json.loads(out.read(1024*1024+1))
            if not isinstance(value,dict) or set(value)!={'pass','reason'} or type(value['pass']) is not bool or not isinstance(value['reason'],str) or len(value['reason'])>4096: raise ValueError()
        except (ValueError,TypeError,UnicodeError): return {'pass':False,'reason':'invalid_checker_json','outcome':'checker_error'}
        return {**value,'outcome':'passed' if value['pass'] else 'wrong_answer'}


def execute(client,task,pair,repeat,output,provenance):
    started=time.monotonic();deadline=started+task.timeout
    folder=Path(output)/f'{task.id}-{repeat}'
    folder.mkdir(parents=True,exist_ok=False)
    row={'task_id':task.id,'repeat':repeat,'pass':False,'reason':'not_started','outcome':'runner_error',
         'tokens_in':None,'tokens_out':None,'seconds':None,'pair':pair['id'],'pair_config':pair,
         'mode':pair['mode'],'model_version':None,'run_ids':[],'trace_ids':[],'trace_id':None,'session_id':None,
         'fixture_sha256':tree_digest(task.directory/'fixture'),'task_sha256':hashlib.sha256((task.directory/'task.yaml').read_bytes()).hexdigest(),
         'checker_sha256':hashlib.sha256((task.directory/'check.py').read_bytes()).hexdigest(),
         'checker_support_sha256':hashlib.sha256((Path(__file__).resolve().parents[1]/'checks.py').read_bytes()).hexdigest(),'cleanup':'not_created',**provenance}
    sid=None;cursor=0;usage={'tokens_in':0,'tokens_out':0};responses=0
    def remaining(cap=1800):
        budget=int(deadline-time.monotonic())
        if budget<1: raise TimeoutError()
        return min(cap,budget)
    def event(value):
        nonlocal cursor
        if value['event_id']<=cursor: return
        cursor=value['event_id']
        with (folder/'events.jsonl').open('a') as f: f.write(json.dumps(value,ensure_ascii=False)+'\n')
    def collect(path):
        metadata=client.request('session.collect',timeout=remaining(30),session_id=sid)
        client.download(metadata,path,timeout=remaining(120))
        return metadata
    try:
        client.request('connect',timeout=remaining(10))
        created=client.request('session.create',timeout=remaining(30),pair_id=pair['id'],workdir={'kind':'fixture','source_id':task.source_id,'mode':'rw'})
        sid=created['session_id'];row['session_id']=sid;row['cleanup']='pending'
        expected=hashlib.sha256(json.dumps(pair,sort_keys=True).encode()).hexdigest()
        if created.get('pair_sha256')!=expected: raise ValueError('pair_mismatch')
        # Compare actual initial /work to the local fixture, not just a source_id label.
        with tempfile.TemporaryDirectory(prefix='baf-initial-') as temp:
            archive=Path(temp)/'initial.tar';collect(archive)
            work=Path(temp)/'work';unpack(archive,work)
            if tree_digest(work)!=row['fixture_sha256']: raise ValueError('fixture_mismatch')
        for message in task.messages:
            accepted=client.request('run.start',timeout=remaining(pair['timeout_s']),session_id=sid,message=message)
            row['run_ids'].append(accepted['run_id']);row['trace_ids'].append(accepted['trace_id']);row['trace_id']=accepted['trace_id']
            result=client.wait(sid,accepted['run_id'],after=cursor,on_event=event,timeout=remaining())
            responses+=1
            for key in usage:
                n=result['usage'].get(key)
                usage[key]=None if usage[key] is None or n is None else usage[key]+n
            model=result.get('model')
            if row['model_version'] is not None and model!=row['model_version']: raise ValueError('model_changed')
            row['model_version']=model
            if result.get('mode') not in (None,pair['mode']): raise ValueError('mode_mismatch')
            if result['status']!='completed':
                error=result.get('error') or {}
                row.update(outcome='timeout' if result['status']=='timed_out' else ('cancelled' if result['status']=='cancelled' else 'agent_error'),reason=error.get('code','agent_failed'))
                return row
            if model is None: raise ValueError('missing_model_provenance')
        archive=folder/'work.tar'
        metadata=collect(archive);row['artifact_sha256']=metadata['sha256']
        with tempfile.TemporaryDirectory(prefix='baf-final-') as temp:
            unpack(archive,temp)
            result=check(task.directory/'check.py',temp,timeout=remaining(10))
            (folder/'checker.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
            row.update(result)
    except TimeoutError:
        row.update(outcome='timeout',reason='task_deadline')
    except ClientError as exc:
        code=str(exc)
        row.update(outcome='timeout' if code=='client_deadline' else 'transport_error',reason=code if re.fullmatch('[a-zA-Z0-9_-]{1,80}',code) else 'client_error')
    except (OSError,ValueError,KeyError,tarfile.TarError):
        row.update(outcome='runner_error',reason='invalid_fixture_pair_artifact_or_response')
    finally:
        if responses: row.update(usage)
        if sid:
            try:
                client.request('session.close',timeout=45,session_id=sid)
                row['cleanup']='closed'
            except Exception:
                row['original_outcome']=row['outcome']
                row.update(cleanup='failed',outcome='cleanup_error',reason='session_close_failed',**{'pass':False})
        row['seconds']=round(time.monotonic()-started,6)
        (folder/'result.json').write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n')
    return row
