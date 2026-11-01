"""Run the reusable smoke script as a process against an isolated HTTP fixture."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import ssl
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest import mock
from urllib.error import URLError
from wsgiref.simple_server import WSGIRequestHandler, make_server

from app.web import create_app
from scripts import smoke_test

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'smoke_test.py'


class SmokeScriptTests(unittest.TestCase):
    def setUp(self):
        self.responses = {'/health': (200, {'status': 'ok'}), '/api/contact': (201, {'ok': True})}
        self.requests = []
        responses, requests = self.responses, self.requests

        class Handler(BaseHTTPRequestHandler):
            def handle_request(self):
                length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(length)
                requests.append((self.command, self.path, body))
                code, payload = responses[self.path]
                encoded = json.dumps(payload).encode()
                self.send_response(code)
                if code == 302:
                    self.send_header('Location', '/unexpected-redirect')
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            do_GET = handle_request
            do_POST = handle_request

            def log_message(self, format, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def run_script(self, url=None, direct=False):
        command = [str(SCRIPT)] if direct else [sys.executable, str(SCRIPT)]
        return subprocess.run(command + [self.url if url is None else url],
                              text=True, capture_output=True, timeout=10)

    def test_success_checks_both_and_sends_identifiable_synthetic_data(self):
        result = self.run_script(direct=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS GET /health', result.stdout)
        self.assertIn('PASS POST /api/contact', result.stdout)
        self.assertIn('PASS smoke test', result.stdout)
        self.assertEqual([(m, p) for m, p, _ in self.requests], [('GET', '/health'), ('POST', '/api/contact')])
        payload = json.loads(self.requests[1][2])
        self.assertEqual(payload['email'], 'smoke@example.com')
        self.assertTrue(payload['subject'].startswith('smoke-test-'))
        self.assertIn(payload['subject'], result.stdout)

    def test_script_against_actual_wsgi_application(self):
        saved = []
        class Backend:
            def submit_contact(self, values):
                saved.append(values)
        class QuietHandler(WSGIRequestHandler):
            def log_message(self, format, *args):
                pass
        server = make_server('127.0.0.1', 0, create_app(Backend()), handler_class=QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = self.run_script(f'http://127.0.0.1:{server.server_port}')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(len(saved), 1)
            self.assertIn(saved[0]['subject'], result.stdout)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_health_failure_still_checks_contact_and_fails(self):
        self.responses['/health'] = (503, {'status': 'error'})
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn('FAIL GET /health: HTTP 503', result.stdout)
        self.assertIn('PASS POST /api/contact', result.stdout)
        self.assertEqual(len(self.requests), 2)

    def test_bad_contact_response_and_redirect_fail(self):
        for response in ((201, {'ok': False}), (201, {'ok': 1}), (200, {'ok': True}), (302, {})):
            with self.subTest(response=response):
                self.responses['/api/contact'] = response
                result = self.run_script()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn('FAIL POST /api/contact', result.stdout)
                self.assertIn('FAIL smoke test', result.stdout)

    def test_network_failure_messages_identify_common_causes(self):
        cases = [(ssl.SSLCertVerificationError('bad cert'), 'TLS certificate verification failed'),
                 (socket.gaierror('bad dns'), 'DNS lookup failed'),
                 (socket.timeout('slow'), 'timed out'),
                 (ConnectionRefusedError('refused'), 'connection refused')]
        for reason, expected in cases:
            with self.subTest(expected=expected):
                opener = mock.Mock()
                opener.open.side_effect = URLError(reason)
                ok, detail = smoke_test.check(opener, self.url + '/health', 'GET', 200, 'status', 'ok')
                self.assertFalse(ok)
                self.assertIn(expected, detail)

    def test_unreachable_origin_returns_failure_for_both_checks(self):
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0))
            port = reserved.getsockname()[1]
            # A bound but non-listening socket rejects connections without a port race.
            result = self.run_script(f'http://127.0.0.1:{port}')
        self.assertEqual(result.returncode, 1)
        self.assertIn('FAIL GET /health: connection refused', result.stdout)
        self.assertIn('FAIL POST /api/contact: connection refused', result.stdout)
        self.assertIn('FAIL smoke test', result.stdout)

    def test_invalid_origin_returns_nonzero_without_requests(self):
        for url in ('ftp://example.com', 'https://user:secret@example.com', self.url + '/path', 'http://localhost:bad'):
            with self.subTest(url=url):
                result = self.run_script(url)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.requests, [])


if __name__ == '__main__':
    unittest.main()
