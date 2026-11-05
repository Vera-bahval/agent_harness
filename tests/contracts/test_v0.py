"""Offline schema tests. Runtime guarantees belong to tests/infra."""
import copy
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = {}
        cls.validators = {}
        cls.examples = {}
        for side in ('client-server', 'harness-sandbox'):
            schema = json.loads((ROOT / 'contracts' / f'{side}.v0.schema.json').read_text())
            Draft202012Validator.check_schema(schema)
            cls.schemas[side] = schema
            cls.validators[side] = Draft202012Validator(schema)
            cls.examples[side] = json.loads((ROOT / 'contracts/examples' / f'{side}.json').read_text())

    def example(self, side, kind):
        return copy.deepcopy(next(x for x in self.examples[side] if x['type'] == kind))

    def assert_invalid(self, side, value):
        self.assertFalse(self.validators[side].is_valid(value), repr(value))

    def test_examples_valid_without_network(self):
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('Network prohibited')):
            for side, examples in self.examples.items():
                for message in examples:
                    with self.subTest(side=side, kind=message['type']):
                        self.validators[side].validate(message)

    def test_every_message_variant_has_example(self):
        for side, schema in self.schemas.items():
            expected = {item['$ref'].split('/')[-1] for item in schema['oneOf']}
            self.assertEqual(expected, {x['type'] for x in self.examples[side]})

    def test_unknown_version_type_and_extra_fields_rejected(self):
        for side in self.examples:
            for message in self.examples[side]:
                for key, value in [('version', 'v1'), ('type', 'unknown'), ('api_key', 'canary')]:
                    with self.subTest(side=side, kind=message['type'], key=key):
                        altered = copy.deepcopy(message)
                        altered[key] = value
                        self.assert_invalid(side, altered)

    def test_every_required_field_is_enforced(self):
        for side, examples in self.examples.items():
            for message in examples:
                for field in self.schemas[side]['$defs'][message['type']]['required']:
                    altered = copy.deepcopy(message)
                    del altered[field]
                    with self.subTest(side=side, kind=message['type'], missing=field):
                        self.assert_invalid(side, altered)

    def test_timeout_and_cursor_bounds(self):
        for side in self.examples:
            req = self.example(side, 'events.subscribe')
            for timeout in (0, -1, 1801, '10', True):
                self.assert_invalid(side, {**req, 'timeout_s': timeout})
            for timeout in (1, 1800):
                self.validators[side].validate({**req, 'timeout_s': timeout})
            self.assert_invalid(side, {**req, 'after_event_id': -1})
            self.assert_invalid(side, {**req, 'expires_at': 0})

    def test_workdir_is_reference_not_host_path(self):
        for side, kind in [('client-server', 'session.create'), ('harness-sandbox', 'provision')]:
            req = self.example(side, kind)
            for change in ({'host_path': '/etc'}, {'mode': 'root'}, {'source_id': '../secrets'}):
                altered = copy.deepcopy(req)
                altered['workdir'].update(change)
                self.assert_invalid(side, altered)

    def test_tools_and_binary_content(self):
        side = 'harness-sandbox'
        req = self.example(side, 'exec')
        for command in (
            {'name': 'ReadFile', 'path': 'config/app.yaml'},
            {'name': 'WriteFile', 'path': 'answer.txt', 'content_base64': 'ODEzNw=='},
            {'name': 'WriteFile', 'path': 'empty.txt', 'content_base64': ''},
            {'name': 'RunCommand', 'argv': ['printf', ''], 'cwd': '.'},
        ):
            self.validators[side].validate({**req, 'command': command})
        for command in (
            {'name': 'RunCommand', 'argv': [], 'cwd': '.'},
            {'name': 'RunCommand', 'argv': [''], 'cwd': '.'},
            {'name': 'RunCommand', 'argv': ['env'], 'cwd': '.', 'env': {'KEY': 'canary'}},
            {'name': 'WriteFile', 'path': 'x', 'content_base64': 'not base64!'},
            {'name': 'WriteFile', 'path': 'x', 'content': 'text'},
            {'name': 'DeleteFile', 'path': 'x'},
        ):
            self.assert_invalid(side, {**req, 'command': command})

    def test_path_traversal_and_absolute_paths(self):
        side = 'harness-sandbox'
        req = self.example(side, 'exec')
        for path in ('/etc/passwd', '..', '../x', 'a/../x', 'a/..', 'a\\b', 'a\x00b', ''):
            for command in ({'name': 'ReadFile', 'path': path}, {'name': 'RunCommand', 'argv': ['pwd'], 'cwd': path}):
                with self.subTest(path=path):
                    self.assert_invalid(side, {**req, 'command': command})
        self.validators[side].validate({**req, 'command': {'name': 'ReadFile', 'path': 'a..b'}})

    def test_terminal_state_requires_result_and_running_forbids_it(self):
        for side, kind in [('client-server', 'run.state'), ('harness-sandbox', 'operation.state')]:
            msg = self.example(side, kind)
            self.assert_invalid(side, {**msg, 'result': None})
            self.assert_invalid(side, {**msg, 'state': 'running'})
            self.validators[side].validate({**msg, 'state': 'running', 'result': None})

    def test_failure_needs_error_and_success_forbids_error(self):
        error = {'class': 'command_error', 'code': 'nonzero_exit', 'message': 'exit 1', 'retry': 'never'}
        for side, kind in [('client-server', 'run.result'), ('harness-sandbox', 'exec.result')]:
            msg = self.example(side, kind)
            bad = copy.deepcopy(msg)
            bad['result']['error'] = error
            self.assert_invalid(side, bad)
            bad['result']['status'] = 'failed'
            self.validators[side].validate(bad)
            bad['result']['error'] = None
            self.assert_invalid(side, bad)

    def test_command_exit_code_and_output_limits(self):
        side = 'harness-sandbox'
        msg = self.example(side, 'exec.result')
        for code in (1, -1, 256, '0'):
            bad = copy.deepcopy(msg)
            bad['result']['exit_code'] = code
            self.assert_invalid(side, bad)
        msg['result']['stdout'] = 'x' * 65537
        self.assert_invalid(side, msg)
        output = self.example(side, 'exec.output')
        self.assert_invalid(side, {**output, 'text': 'x' * 4097})

    def test_all_error_classes_and_recovery_policies(self):
        for side in self.examples:
            msg = self.example(side, 'error')
            for category in ('protocol_error', 'command_error', 'timeout', 'sandbox_error', 'transport_error'):
                for retry in ('never', 'query_status', 'same_request'):
                    msg['error'].update({'class': category, 'retry': retry})
                    self.validators[side].validate(msg)
            msg['error']['retry'] = 'blind_retry'
            self.assert_invalid(side, msg)


if __name__ == '__main__':
    unittest.main()
