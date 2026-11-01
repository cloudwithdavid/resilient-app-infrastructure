"""Optional HTTP-to-database check for an isolated Apache/application stack."""
import json
import os
import socket
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from uuid import uuid4

import pymysql


@unittest.skipUnless(os.environ.get('TEST_APACHE_URL') and os.environ.get('TEST_MYSQL_SOCKET'),
                     'set TEST_APACHE_URL and TEST_MYSQL_SOCKET for an isolated local stack')
class ApacheIntegrationTests(unittest.TestCase):
    def request(self, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(os.environ['TEST_APACHE_URL'].rstrip('/') + path, data=data,
                          headers={'Content-Type': 'application/json'} if data else {})
        try:
            with urlopen(request, timeout=15) as response:
                return response.status, response.read()
        except HTTPError as error:
            return error.code, error.read()

    def test_oversized_and_ambiguous_http_framing_rejected(self):
        url = os.environ['TEST_APACHE_URL'].rstrip('/') + '/api/contact'
        request = Request(url, data=b'x' * 16385, headers={'Content-Type': 'application/json'})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 413)
        parsed = urlsplit(os.environ['TEST_APACHE_URL'])
        self.assertEqual(parsed.scheme, 'http')  # Local disposable test stack only.
        with socket.create_connection((parsed.hostname, parsed.port or 80), timeout=5) as connection:
            connection.sendall(b'POST /api/contact HTTP/1.1\r\nHost: localhost\r\n'
                               b'Content-Length: 10\r\nTransfer-Encoding: chunked\r\n'
                               b'Content-Type: application/json\r\nConnection: close\r\n\r\n0\r\n\r\n')
            with connection.makefile('rb') as response:
                self.assertIn(b' 400 ', response.readline())

    def test_static_health_post_and_persistence(self):
        status, page = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn(b'Send a Message', page)
        self.assertEqual(self.request('/css/style.css')[0], 200)
        status, body = self.request('/health')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['status'], 'ok')
        marker = 'apache-test-' + str(uuid4())
        admin = pymysql.connect(unix_socket=os.environ['TEST_MYSQL_SOCKET'], user='root',
                                database='resilient_test', autocommit=True)
        try:
            status, body = self.request('/api/contact', {'name': 'Demo', 'email': 'demo@example.com',
                                                         'subject': marker, 'message': 'Synthetic via Apache'})
            self.assertEqual(status, 201)
            self.assertTrue(json.loads(body)['ok'])
            with admin.cursor() as cursor:
                cursor.execute('SELECT message FROM contact_submissions WHERE subject = %s', (marker,))
                self.assertEqual(cursor.fetchall(), (('Synthetic via Apache',),))
            self.assertEqual(self.request('/api/contact', {'name': 'missing other fields'})[0], 400)
            self.assertEqual(self.request('/api/contact/extra')[0], 404)
        finally:
            with admin.cursor() as cursor:
                cursor.execute('DELETE FROM contact_submissions WHERE subject = %s', (marker,))
            admin.close()
