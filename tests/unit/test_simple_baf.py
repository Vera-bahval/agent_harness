import json
from pathlib import Path
import shutil
import tempfile
import unittest

from baf.runner.core import check,discover
from services.common.fixtures import SOURCES

ROOT=Path(__file__).resolve().parents[2]


class SimpleBafTests(unittest.TestCase):
    def setUp(self):
        self.tasks=discover(ROOT/'baf')
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def workspace(self,identifier):
        task=self.tasks[identifier]
        work=self.root/identifier
        shutil.copytree(task.directory/'fixture',work)
        return task,work

    def passed(self,task,work):
        result=check(task.directory/'check.py',work)
        self.assertIn(result['outcome'],('passed','wrong_answer'),result)
        return result['pass']

    def test_registry_matches_five_simple_tasks(self):
        self.assertTrue({f'B{i}.1' for i in range(1,6)}<=set(self.tasks))
        for task in self.tasks.values():
            self.assertIn(task.source_id,SOURCES)
            self.assertFalse((task.directory/'fixture/check.py').exists())

    def test_search_definition_not_callsite(self):
        task,work=self.workspace('B2.1')
        self.assertFalse(self.passed(task,work))
        for answer,expected in [('app/helpers/dates.py',True),('app/main.py',False),('tests/test_dates.py',False),('/work/app/helpers/dates.py',False)]:
            with self.subTest(answer=answer):
                (work/'answer.txt').write_text(answer+'\n')
                self.assertEqual(self.passed(task,work),expected)

    def test_config_schema_types_required_and_extra_keys(self):
        task,work=self.workspace('B3.1')
        valid={'name':'agent-service','port':8085,'debug':False,'retries':3,'environments':['dev','test']}
        self.assertFalse(self.passed(task,work))
        for value,expected in [(valid,True),({**valid,'debug':'false'},False),({**valid,'port':'8085'},False),
                               ({**valid,'extra':1},False),({k:v for k,v in valid.items() if k!='retries'},False),
                               ({**valid,'environments':['test','dev']},False)]:
            with self.subTest(value=value):
                (work/'config.json').write_text(json.dumps(value))
                self.assertEqual(self.passed(task,work),expected)
        (work/'config.json').write_text(json.dumps(valid)[:-1]+',"port":8085}')
        self.assertFalse(self.passed(task,work))

    def test_edit_checks_all_bytes_and_other_files(self):
        task,work=self.workspace('B4.1')
        original=(work/'README.md').read_bytes()
        expected=original.replace(b'pip install example-client==1.2',b'pip install example-client==1.3')
        for value,passed in [(original,False),(expected,True),(original.replace(b'1.2',b'1.3'),False),
                             (expected.replace(b'\n',b'\r\n'),False),(expected+b'\n',False)]:
            with self.subTest(value=value):
                (work/'README.md').write_bytes(value)
                self.assertEqual(self.passed(task,work),passed)
        (work/'README.md').write_bytes(expected)
        (work/'notes.txt').write_text('changed')
        self.assertFalse(self.passed(task,work))

    def test_median_not_mean_and_inputs_unchanged(self):
        task,work=self.workspace('B5.1')
        self.assertFalse(self.passed(task,work))
        for answer,passed in [('7',True),('7.0',True),('21.6666666667',False),('6',False),('NaN',False),('median: 7',False)]:
            with self.subTest(answer=answer):
                (work/'answer.txt').write_text(answer)
                self.assertEqual(self.passed(task,work),passed)
        (work/'answer.txt').write_text('7')
        (work/'data.csv').write_text('value\n7\n')
        self.assertFalse(self.passed(task,work))


if __name__=='__main__': unittest.main()
