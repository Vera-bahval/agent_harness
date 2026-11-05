"""Host-side integration of public gateway, CLI library and Docker sandbox (replay only)."""
import io
import sys
from pathlib import Path
import tarfile
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from client.cli import Client,ClientError


class ClientIntegration(unittest.TestCase):
    def setUp(self):
        self.client=Client('http://127.0.0.1:8080',Path('.local/client.token').read_text().strip())
        self.client.request('connect')

    def session(self,pair='replay-b1'):
        sid=self.client.request('session.create',timeout=30,pair_id=pair,workdir={'kind':'fixture','source_id':'B1_1','mode':'rw'})['session_id']
        self.addCleanup(self.client.request,'session.close',timeout=45,session_id=sid)
        return sid

    def run_task(self,sid,message='порт',timeout=60):
        run=self.client.request('run.start',timeout=timeout,session_id=sid,message=message)
        events=[]
        result=self.client.wait(sid,run['run_id'],on_event=events.append,timeout=90)
        return run,result,events

    def test_full_public_path_collect_and_schema_events(self):
        import json
        from jsonschema import Draft202012Validator
        validator=Draft202012Validator(json.loads(Path('contracts/client-server.v0.schema.json').read_text()))
        sid=self.session()
        run,result,events=self.run_task(sid)
        self.assertEqual(result['status'],'completed',result)
        self.assertTrue(all(validator.is_valid(e) for e in events))
        self.assertEqual(sum(e['type']=='run.result' for e in events),1)
        self.assertEqual(sum(e['type']=='tool.started' for e in events),3)
        metadata=self.client.request('session.collect',timeout=30,session_id=sid)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'work.tar'
            self.client.download(metadata,path)
            with tarfile.open(path) as archive: self.assertEqual(archive.extractfile('answer.txt').read(),b'8137')
        self.client.request('session.close',timeout=30,session_id=sid)
        with self.assertRaisesRegex(ClientError,'not_found'): self.client.open('/v0/artifacts/'+metadata['artifact_id'])

    def test_second_turn_keeps_workdir_and_history(self):
        sid=self.session('replay-session')
        _,first,_=self.run_task(sid,'remember')
        self.assertEqual(first['status'],'completed',first)
        _,second,events=self.run_task(sid,'recall')
        self.assertEqual(second['status'],'completed',second)
        self.assertEqual(second['answer'],'Recalled persistent-42')
        self.assertIn('ReadFile',[e.get('name') for e in events])

    def test_cancel_reconnect_and_conflicting_run(self):
        sid=self.session('replay-slow')
        run=self.client.request('run.start',timeout=60,session_id=sid,message='slow')
        stream=self.client.events(sid,timeout=5)
        first=next(stream);stream.close()
        with self.assertRaisesRegex(ClientError,'conflict'): self.client.request('run.start',timeout=60,session_id=sid,message='duplicate')
        self.client.request('run.cancel',session_id=sid,run_id=run['run_id'])
        seen=[]
        result=self.client.wait(sid,run['run_id'],after=first['event_id'],on_event=seen.append,timeout=60)
        self.assertEqual(result['status'],'cancelled',result)
        self.assertTrue(all(e['event_id']>first['event_id'] for e in seen))


if __name__=='__main__': unittest.main(verbosity=2)
