"""Exercise the real Gunicorn thread pool while dependencies are blocked."""
import concurrent.futures
import json
import os
import shlex
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class GunicornHealthTests(unittest.TestCase):
    def test_liveness_stays_available_during_slow_and_failed_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'health_fixture.py').write_text('''
import os, time, uuid
from pathlib import Path
from app.backend import DiagnosticError
from app.web import create_app
root = Path(os.environ["HEALTH_TEST_DIR"])
class SlowBackend:
    def submit_contact(self, values):
        if (root / "fail").exists():
            raise DiagnosticError("database_connection_error", "unavailable")
        (root / ("started-" + str(uuid.uuid4()))).touch()
        deadline = time.monotonic() + 15
        while not (root / "release").exists() and time.monotonic() < deadline:
            time.sleep(.02)
application = create_app(SlowBackend())
''')
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            env = {**os.environ, 'HEALTH_TEST_DIR': directory,
                   'PYTHONPATH': os.pathsep.join((directory, os.getcwd()))}
            service = Path('app/deploy/resilient-infra.service').read_text()
            command = shlex.split(next(line.removeprefix('ExecStart=') for line in service.splitlines()
                                       if line.startswith('ExecStart=')))
            self.assertIn('--no-control-socket', command)
            self.assertEqual(command[command.index('--worker-class') + 1], 'gthread')
            threads = command[command.index('--threads') + 1]
            self.assertGreater(int(threads), 2)
            process = subprocess.Popen([sys.executable, '-m', 'gunicorn', '--no-control-socket', '--bind', f'127.0.0.1:{port}',
                                        '--workers', '1', '--worker-class', 'gthread', '--threads', threads,
                                        'health_fixture:application'], env=env,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.addCleanup(self.stop_process, process)
            url = f'http://127.0.0.1:{port}'
            def request(path, data=None):
                req = Request(url + path, data=data,
                              headers={'Content-Type': 'application/json'} if data is not None else {})
                try:
                    with urlopen(req, timeout=3) as response:
                        return response.status, json.loads(response.read())
                except HTTPError as error:
                    return error.code, json.loads(error.read())
            deadline = time.monotonic() + 10
            while True:
                try:
                    self.assertEqual(request('/health')[0], 200)
                    break
                except (URLError, ConnectionError):
                    if process.poll() is not None or time.monotonic() > deadline:
                        self.fail('Gunicorn did not start')
                    time.sleep(.05)
            payload = json.dumps({'name': 'Demo', 'email': 'demo@example.com',
                                  'subject': 'Test', 'message': 'Synthetic'}).encode()
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                pending = [pool.submit(request, '/api/contact', payload) for _ in range(2)]
                try:
                    deadline = time.monotonic() + 3
                    while len(list(root.glob('started-*'))) < 2:
                        if time.monotonic() > deadline:
                            self.fail('Slow requests did not reach the backend')
                        time.sleep(.02)
                    self.assertEqual(request('/health'), (200, {'status': 'ok'}))
                    self.assertEqual(request('/api/contact', payload)[0], 503)
                finally:
                    (root / 'release').touch()
                self.assertEqual([item.result()[0] for item in pending], [201, 201])
            (root / 'fail').touch()
            self.assertEqual(request('/api/contact', payload)[0], 503)
            self.assertEqual(request('/health')[0], 200)

    @staticmethod
    def stop_process(process):
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
