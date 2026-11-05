import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest

from baf.runner.core import check,discover,execute,unpack
from client.cli import ClientError
from services.common.pairs import load_pairs

ROOT=Path(__file__).resolve().parents[2]


class FakeClient:
    def __init__(self,task,pair,answer=b'8137',failure=None,close_failure=False):
        self.task,self.pair,self.answer=task,pair,answer
        self.failure,self.close_failure=failure,close_failure
        self.closed=False;self.collects=0;self.started=False
    def request(self,kind,**kwargs):
        if kind=='connect': return {}
        if kind=='session.create': return {'session_id':'session','pair_sha256':hashlib.sha256(json.dumps(self.pair,sort_keys=True).encode()).hexdigest()}
        if kind=='session.collect': self.collects+=1;return {}
        if kind=='run.start': self.started=True;return {'run_id':'run','trace_id':'trace'}
        if kind=='session.close':
            self.closed=True
            if self.close_failure: raise ClientError('cleanup_failed')
            return {}
        raise AssertionError(kind)
    def wait(self,*args,**kwargs):
        if self.failure=='transport': raise ClientError('unknown_outcome')
        return {'status':'timed_out' if self.failure=='timeout' else 'completed','answer':'done',
                'error':{'code':'deadline_exceeded'} if self.failure else None,'usage':{'tokens_in':None,'tokens_out':None},'model':'scripted-v0','mode':'replay'}
    def download(self,metadata,path,timeout):
        with tarfile.open(path,'w') as archive:
            for entry in sorted((self.task.directory/'fixture').iterdir()): archive.add(entry,arcname=entry.name)
            if self.collects>1 and self.answer is not None:
                item=tarfile.TarInfo('answer.txt');item.size=len(self.answer)
                archive.addfile(item,io.BytesIO(self.answer))
        metadata['sha256']=hashlib.sha256(Path(path).read_bytes()).hexdigest()


class BafRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.task=discover(ROOT/'baf')['B1.1']
        self.pair=load_pairs(ROOT/'configs/pairs')['replay-b1']

    def test_checker_correct_wrong_and_missing_answer(self):
        for answer,passed in [(None,False),('8080',False),('8137',True)]:
            path=self.root/'answer.txt'
            if answer is not None: path.write_text(answer)
            value=check(self.task.directory/'check.py',self.root)
            self.assertEqual(value['pass'],passed)
            self.assertEqual(value['outcome'],'passed' if passed else 'wrong_answer')

    def test_checker_invalid_json_nonzero_and_timeout(self):
        checker=self.root/'check.py'
        for code,outcome in [('print("not JSON")','checker_error'),('raise SystemExit(1)','checker_error'),
                             ('import time; time.sleep(10)','checker_timeout'),('print(\'{"pass": "yes", "reason":"bad"}\')','checker_error')]:
            checker.write_text(code)
            self.assertEqual(check(checker,self.root,timeout=.1)['outcome'],outcome)

    def test_checker_does_not_receive_client_or_provider_secrets(self):
        checker=self.root/'check.py'
        checker.write_text('import os,json; print(json.dumps({"pass":not any(k in os.environ for k in ("CLIENT_TOKEN","LLM_API_KEY")),"reason":"env"}))')
        from unittest.mock import patch
        with patch.dict('os.environ',{'CLIENT_TOKEN':'canary','LLM_API_KEY':'canary'}):
            self.assertTrue(check(checker,self.root)['pass'])

    def test_unsafe_archives_are_rejected(self):
        for index,(name,kind) in enumerate([('../escape','file'),('/absolute','file'),('link','symlink'),('fifo','fifo')]):
            path=self.root/f'{index}.tar'
            with tarfile.open(path,'w') as archive:
                item=tarfile.TarInfo(name)
                if kind=='symlink': item.type=tarfile.SYMTYPE;item.linkname='/etc/passwd'
                if kind=='fifo': item.type=tarfile.FIFOTYPE
                archive.addfile(item)
            with self.assertRaises(ValueError): unpack(path,self.root/f'out{index}')

    def test_duplicate_archive_entries_are_rejected(self):
        path=self.root/'duplicate.tar'
        with tarfile.open(path,'w') as archive:
            archive.addfile(tarfile.TarInfo('x'));archive.addfile(tarfile.TarInfo('x'))
        with self.assertRaises(ValueError): unpack(path,self.root/'out')

    def test_discovery_rejects_duplicate_ids_and_missing_fields(self):
        shutil.copytree(ROOT/'baf/B1-read',self.root/'B1-read')
        shutil.copy(ROOT/'baf/fixtures.json',self.root/'fixtures.json')
        original=self.root/'B1-read/B1.1-service-port'
        shutil.copytree(original,self.root/'duplicate')
        with self.assertRaisesRegex(ValueError,'duplicate_task_id'): discover(self.root)
        shutil.rmtree(self.root/'duplicate')
        (original/'task.yaml').write_text('id: B1.1\n')
        with self.assertRaisesRegex(ValueError,'invalid_task_fields'): discover(self.root)

    def test_runner_pass_wrong_missing_and_cleanup(self):
        for repeat,answer in enumerate([b'8137',b'8080',None],1):
            client=FakeClient(self.task,self.pair,answer)
            row=execute(client,self.task,self.pair,repeat,self.root,{})
            self.assertEqual(row['pass'],answer==b'8137',row)
            self.assertTrue(client.closed)
            self.assertEqual(row['cleanup'],'closed')
            self.assertIsNone(row['tokens_in'])
            self.assertEqual(row['model_version'],'scripted-v0')

    def test_runner_timeout_transport_and_cleanup_failure(self):
        for repeat,(failure,close_failure,expected) in enumerate([('timeout',False,'timeout'),('transport',False,'transport_error'),(None,True,'cleanup_error')],1):
            client=FakeClient(self.task,self.pair,failure=failure,close_failure=close_failure)
            row=execute(client,self.task,self.pair,repeat,self.root,{})
            self.assertEqual(row['outcome'],expected,row)
            self.assertFalse(row['pass']);self.assertTrue(client.closed)

    def test_initial_fixture_mismatch_stops_before_model(self):
        client=FakeClient(self.task,self.pair)
        original=client.download
        def bad(metadata,path,timeout):
            original(metadata,path,timeout)
            with tarfile.open(path,'a') as archive: archive.addfile(tarfile.TarInfo('unexpected'))
        client.download=bad
        row=execute(client,self.task,self.pair,1,self.root,{})
        self.assertFalse(row['pass']);self.assertFalse(client.started);self.assertTrue(client.closed)


if __name__=='__main__': unittest.main()
