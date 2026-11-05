"""Offline end-to-end harness/proxy/sandbox tests. Runs in the harness container."""
import io
import json
from pathlib import Path
import tarfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from uuid import uuid4

BASE='http://127.0.0.1:8000'


def call(path,data=None):
    try:
        response=urlopen(Request(BASE+path,data=json.dumps(data).encode() if data is not None else None,
                                  headers={'Content-Type':'application/json'}),timeout=10)
    except HTTPError as error:
        response=error
    with response:
        return response.status,json.load(response)


class HarnessTests(unittest.TestCase):
    def start(self,pair='replay-b1',timeout=60,prompt=None):
        identifier=uuid4().hex
        payload={'run_id':identifier,'pair_id':pair,'prompt':prompt or 'Прочитай порт и запиши в answer.txt',
                 'workdir':{'kind':'fixture','source_id':'B1_1','mode':'rw'},'timeout_s':timeout}
        status,value=call('/internal/runs',payload)
        self.assertEqual(status,202,value)
        self.addCleanup(call,'/internal/runs/'+identifier+'/cancel',{})
        return identifier,payload

    def wait(self,identifier):
        end=time.monotonic()+40
        while time.monotonic()<end:
            status,state=call('/internal/runs/'+identifier)
            self.assertEqual(status,200)
            if state['state']=='terminal':
                self.assertEqual(state['result']['cleanup'],'released',state)
                return state
            time.sleep(.05)
        self.fail('Harness did not finish')

    def events(self,identifier):
        return call('/internal/runs/'+identifier+'/trace')[1]['events']

    def test_full_loop_creates_file_and_trace(self):
        identifier,_=self.start()
        state=self.wait(identifier)
        self.assertEqual(state['result']['status'],'completed',state)
        self.assertEqual(state['result']['mode'],'replay')
        self.assertEqual(state['result']['usage'],{'tokens_in':None,'tokens_out':None})
        with urlopen(BASE+'/internal/runs/'+identifier+'/artifact',timeout=10) as response:
            raw=response.read()
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            self.assertEqual(archive.extractfile('answer.txt').read(),b'8137')
        events=self.events(identifier)
        self.assertEqual([e['name'] for e in events if e['event']=='tool.started'],['ReadFile','WriteFile','RunCommand'])
        self.assertEqual(sum(e['event']=='run.result' for e in events),1)
        self.assertEqual(events[-1]['event'],'run.result')
        self.assertTrue(all(e['trace_id']==state['trace_id'] for e in events))
        token=Path('/run/runtime-auth/sandbox.token').read_text().strip()
        self.assertNotIn(token,json.dumps(events))
        self.assertNotIn('old port',json.dumps(events))

    def test_command_error_is_returned_to_model(self):
        identifier,_=self.start('replay-recovery')
        self.assertEqual(self.wait(identifier)['result']['status'],'completed')
        outcomes=[e['status'] for e in self.events(identifier) if e['event']=='tool.result']
        self.assertEqual(outcomes,['failed','success'])

    def test_invalid_arguments_are_observations(self):
        identifier,_=self.start('replay-invalid')
        self.assertEqual(self.wait(identifier)['result']['status'],'completed')
        errors=[e.get('error_code') for e in self.events(identifier) if e['event']=='tool.result']
        self.assertIn('invalid_tool_path',errors)

    def test_max_steps_stops_loop(self):
        identifier,_=self.start('replay-loop')
        value=self.wait(identifier)['result']
        self.assertEqual(value['status'],'failed')
        self.assertEqual(value['error']['code'],'max_steps')
        self.assertFalse(value['artifact_available'])
        self.assertEqual(sum(e['event']=='llm.result' for e in self.events(identifier)),3)

    def test_cancel_active_tool(self):
        identifier,_=self.start('replay-slow')
        end=time.monotonic()+10
        while time.monotonic()<end:
            if any(e['event']=='tool.started' for e in self.events(identifier)):
                break
            time.sleep(.05)
        self.assertEqual(call('/internal/runs/'+identifier+'/cancel',{})[0],202)
        self.assertEqual(self.wait(identifier)['result']['status'],'cancelled')

    def test_deadline_releases_resources(self):
        identifier,_=self.start('replay-slow',timeout=2)
        self.assertEqual(self.wait(identifier)['result']['status'],'timed_out')

    def test_run_retry_does_not_duplicate_work(self):
        identifier,payload=self.start()
        self.assertEqual(call('/internal/runs',payload)[0],202)
        self.wait(identifier)
        self.assertEqual(call('/internal/runs',payload)[0],202)
        events=self.events(identifier)
        self.assertEqual(sum(e['event']=='run.started' for e in events),1)
        self.assertEqual(sum(e['event']=='sandbox.provisioned' for e in events),1)
        self.assertEqual(call('/internal/runs',{**payload,'prompt':'different'})[0],400)

    def test_replay_checks_prompt(self):
        identifier,_=self.start(prompt='Несовместимый запрос')
        value=self.wait(identifier)['result']
        self.assertEqual(value['status'],'failed')
        self.assertEqual(value['error']['code'],'replay_prompt_mismatch')


if __name__=='__main__':
    unittest.main(verbosity=2)
