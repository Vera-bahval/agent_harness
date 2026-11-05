import json
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from services.common.server import ServiceServer, Settings, check_dependency


class BootstrapTests(unittest.TestCase):
    def start(self, dependencies=None):
        server = ServiceServer(('127.0.0.1', 0), Settings('test-service', dependencies or {}, 0.2))
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return 'http://127.0.0.1:' + str(server.server_port)

    def fetch(self, url, data=None):
        try:
            response = urlopen(Request(url, data=data), timeout=2)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def test_health_ready_with_healthy_dependency(self):
        dependency = self.start()
        url = self.start({'child': dependency})
        status, body = self.fetch(url + '/health')
        self.assertEqual(status, 200)
        self.assertEqual(body['dependencies'], {'child': 'ready'})
        self.assertEqual(body['stage'], 'bootstrap')

    def test_downstream_failure_propagates_but_liveness_remains(self):
        # A real 404 response is not a healthy dependency.
        dependency = self.start()
        middle = self.start({'leaf': dependency + '/missing'})
        top = self.start({'middle': middle})
        status, body = self.fetch(top + '/health')
        self.assertEqual(status, 503)
        self.assertEqual(body['dependencies'], {'middle': 'unavailable'})
        self.assertEqual(self.fetch(top + '/live')[0], 200)

    def test_timeout_or_malformed_dependency_not_ready(self):
        for failure in (TimeoutError(), ValueError()):
            with patch('services.common.server.build_opener') as opener:
                opener.return_value.open.side_effect = failure
                self.assertFalse(check_dependency('http://internal', 0.01))

    def test_unimplemented_operations_do_not_report_success(self):
        url = self.start()
        status, body = self.fetch(url + '/v0/requests', b'{"type":"exec"}')
        self.assertEqual(status, 501)
        self.assertEqual(body['error']['code'], 'not_implemented')
        self.assertEqual(body['version'], 'v0')
        self.assertNotIn('exec', body['error']['message'])

    def test_unknown_route_does_not_echo_user_input(self):
        url = self.start()
        status, body = self.fetch(url + '/secret-canary')
        self.assertEqual(status, 404)
        self.assertNotIn('secret-canary', json.dumps(body))


if __name__ == '__main__':
    unittest.main()
