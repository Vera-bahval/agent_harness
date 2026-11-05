import csv
import io
import json
from pathlib import Path
import runpy
import shutil
import tempfile
import unittest

from baf.runner.core import check,discover
from services.common.fixtures import SOURCES

ROOT=Path(__file__).resolve().parents[2]
CHECKS=runpy.run_path(str(ROOT/'baf/checks.py'))


class Cp1Tasks(unittest.TestCase):
    def setUp(self):
        self.tasks=discover(ROOT/'baf')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_catalog_has_fifteen_registered_tasks(self):
        self.assertEqual(set(self.tasks),{f'B{i}.{j}' for i in range(1,6) for j in range(1,4)})
        self.assertTrue(all(t.source_id in SOURCES for t in self.tasks.values()))

    def test_all_new_checkers_accept_correct_and_reject_incorrect(self):
        answers={'B1.2':{'answer.txt':'37'},'B1.3':{'answer.txt':'НЕТ ДАННЫХ'},
                 'B2.2':{'answer.txt':'\n'.join(sorted(CHECKS['READERS']))+'\n'},'B2.3':{'answer.txt':'api/timeparse.py'},
                 'B3.2':{'report.csv':'date,customer,amount\n2025-12-31,Dan,42.00\n2026-01-03,Bob,9.00\n2026-10-01,Carol,75.25\n2026-11-12,Alice,120.50\n'},
                 'B3.3':{'countries.yaml':'norway: "NO"\nontario: "ON"\ndisabled: "no"\nenabled: "on"\n','postal.yaml':'home: "0123"\noffice: "0010"\n'},
                 'B5.2':{'failed.txt':'test_03\ntest_07\ntest_14\n'},
                 'B5.3':{'answer.txt':'no\nmissing required environment variable RELEASE_TOKEN\n'}}
        for identifier,task in self.tasks.items():
            if identifier.endswith('.1'): continue
            with self.subTest(task=identifier):
                work=self.root/identifier;shutil.copytree(task.directory/'fixture',work)
                self.assertFalse(check(task.directory/'check.py',work)['pass'])
                for name,text in answers.get(identifier,{}).items(): (work/name).write_text(text)
                if identifier=='B4.2':
                    for path in work.rglob('*'):
                        if path.is_file():path.write_text(path.read_text().replace('calc_total','compute_total'))
                if identifier=='B4.3':
                    path=work/'prices.csv';path.write_bytes(path.read_bytes().replace(b';850\r\n',b';990\r\n'))
                self.assertTrue(check(task.directory/'check.py',work)['pass'],identifier)
                if identifier=='B4.2': (work/'pricing.py').write_text((work/'pricing.py').read_text()+'\nraise RuntimeError("must never execute")\n')
                elif identifier=='B4.3': (work/'prices.csv').write_bytes((work/'prices.csv').read_bytes().replace(b'\r\n',b'\n'))
                elif identifier=='B3.3': (work/'postal.yaml').write_text('home: 0123\noffice: 0010\n')
                else:
                    first=next(iter(answers[identifier]));(work/first).write_text('WRONG')
                self.assertFalse(check(task.directory/'check.py',work)['pass'])

    def test_long_contract_and_env_fixture_shape(self):
        self.assertGreater((self.tasks['B1.2'].directory/'fixture/contract.md').stat().st_size,40000)
        fixture=self.tasks['B2.2'].directory/'fixture'
        self.assertEqual(len(list(fixture.rglob('module_*.py'))),200)
        self.assertEqual(len(CHECKS['READERS']),12)


if __name__=='__main__':unittest.main()
