"""Optional integration tests for a disposable MySQL-compatible database.

Set TEST_MYSQL_SOCKET, TEST_MYSQL_PORT and TEST_MYSQL_CA for a local server
provisioned as described in app/README.md. Never point this at a shared database.
"""
import io
import json
import os
import unittest
from unittest import mock
from uuid import uuid4

import pymysql
from app import backend, web


@unittest.skipUnless(all(os.environ.get(k) for k in ("TEST_MYSQL_SOCKET", "TEST_MYSQL_PORT", "TEST_MYSQL_CA")),
                     "set TEST_MYSQL_SOCKET, TEST_MYSQL_PORT and TEST_MYSQL_CA for disposable database")
class MySQLIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.admin = pymysql.connect(unix_socket=os.environ["TEST_MYSQL_SOCKET"], user="root", database="resilient_test", autocommit=True)
        self.addCleanup(self.admin.close)
        self.subject = backend.Backend(backend.Settings(region="us-east-1", secret_id="local-test", host="localhost",
                                        database="resilient_test", ca_bundle=os.environ["TEST_MYSQL_CA"],
                                        port=int(os.environ["TEST_MYSQL_PORT"])))
        client = mock.MagicMock()
        client.get_secret_value.return_value = {"SecretString": json.dumps({"username": "resilient_test", "password": "local-test-only"})}
        patcher = mock.patch.object(backend.boto3.session.Session, "client", return_value=client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_health_probe_and_http_submission_through_verified_tls(self):
        self.assertEqual(self.subject.health()["status"], "ok")
        self.assertTrue(self.subject.probe()["cleaned_up"])
        marker = "test-" + str(uuid4())
        self.addCleanup(self.delete_marker, marker)
        payload = {"name": "Demo User", "email": "demo@example.com", "subject": marker,
                   "message": "Synthetic 'quoted' payload 🧪"}
        raw = json.dumps(payload).encode()
        environ = {"PATH_INFO": "/api/contact", "REQUEST_METHOD": "POST", "CONTENT_TYPE": "application/json",
                   "CONTENT_LENGTH": str(len(raw)), "wsgi.input": io.BytesIO(raw)}
        response = {}
        body = b"".join(web.create_app(self.subject)(environ, lambda status, headers: response.update(status=status)))
        self.assertTrue(response["status"].startswith("201"))
        self.assertTrue(json.loads(body)["ok"])
        with self.admin.cursor() as cursor:
            cursor.execute("SELECT name, email, message FROM contact_submissions WHERE subject = %s", (marker,))
            self.assertEqual(cursor.fetchall(), ((payload["name"], payload["email"], payload["message"]),))
            cursor.execute("SELECT COUNT(*) FROM connectivity_probe")
            self.assertEqual(cursor.fetchone()[0], 0)
        with self.subject.connect() as connection:
            with connection.cursor() as cursor:
                for sql in ("SELECT * FROM contact_submissions", "DELETE FROM contact_submissions",
                            "CREATE TABLE forbidden_test_table (id INT)"):
                    with self.subTest(sql=sql), self.assertRaises(pymysql.MySQLError):
                        cursor.execute(sql)

    def test_runtime_account_rejects_plaintext(self):
        with self.assertRaises(pymysql.MySQLError):
            pymysql.connect(host="127.0.0.1", port=int(os.environ["TEST_MYSQL_PORT"]),
                            user="resilient_test", password="local-test-only", database="resilient_test",
                            ssl_disabled=True, connect_timeout=3)

    def test_tls_rejects_wrong_hostname(self):
        settings = backend.Settings(**{**self.subject.settings.__dict__, "host": "127.0.0.1"})
        with self.assertRaises(backend.DiagnosticError):
            backend.Backend(settings).connect()

    def delete_marker(self, marker):
        with self.admin.cursor() as cursor:
            cursor.execute("DELETE FROM contact_submissions WHERE subject = %s", (marker,))
