"""API and database contract tests without AWS; real MySQL coverage is optional."""
import io
import json
import unittest
from unittest import mock

import pymysql
from app import backend, web

VALID = {"name": "Demo User", "email": "demo@example.com", "subject": "Test", "message": "Synthetic test"}


class WebTests(unittest.TestCase):
    def setUp(self):
        self.backend = mock.Mock()
        self.backend.health.return_value = {"status": "ok", "database": "reachable"}
        self.app = web.create_app(self.backend)

    def request(self, path="/api/contact", method="POST", body=None, content_type="application/json", length=None):
        raw = json.dumps(VALID).encode() if body is None else body
        env = {"PATH_INFO": path, "REQUEST_METHOD": method, "CONTENT_TYPE": content_type,
               "CONTENT_LENGTH": str(len(raw)) if length is None else length, "wsgi.input": io.BytesIO(raw)}
        result = {}
        def start(status, headers):
            result["status"] = int(status.split()[0])
            result["headers"] = dict(headers)
        result["body"] = json.loads(b"".join(self.app(env, start)))
        return result

    def test_health_is_independent_of_dependencies_and_configuration(self):
        self.backend.health.side_effect = RuntimeError("downstream unavailable")
        self.assertEqual(self.request("/health", "GET")["body"], {"status": "ok"})
        self.backend.health.assert_not_called()
        with mock.patch.object(web.Settings, "from_env", side_effect=RuntimeError("no config")):
            self.app = web.create_app()
            self.assertEqual(self.request("/health", "GET")["status"], 200)

    def test_valid_submission_is_trimmed_and_stored(self):
        data = {**VALID, "name": "  Demo User  ", "honeypot": ""}
        response = self.request(body=json.dumps(data).encode())
        self.assertEqual(response["status"], 201)
        self.assertEqual(response["body"], {"ok": True})
        self.assertEqual(response["headers"]["Cache-Control"], "no-store")
        self.backend.submit_contact.assert_called_once_with(VALID)

    def test_invalid_submissions_never_reach_database(self):
        for data in ({}, [], {**VALID, "email": "real@somewhere.com"},
                     {**VALID, "message": " "}, {**VALID, "name": "x" * 81},
                     {**VALID, "subject": None}, {**VALID, "honeypot": "spam"},
                     {**VALID, "extra": 1}, {**VALID, "name": "bad\u0000name"}, {**VALID, "message": "\ud800"}):
            with self.subTest(data=data):
                self.assertEqual(self.request(body=json.dumps(data).encode())["status"], 400)
        self.backend.submit_contact.assert_not_called()

    def test_framing_content_type_and_routes(self):
        for kwargs, expected in [
            ({"body": b"{"}, 400), ({"body": b"\xff"}, 400),
            ({"length": "-1"}, 400), ({"length": "bad"}, 400),
            ({"length": "16385"}, 413), ({"length": "200"}, 400),
            ({"content_type": "text/plain"}, 415),
            ({"path": "/api/other"}, 404), ({"path": "/health", "method": "POST"}, 405),
            ({"path": "/api/contact", "method": "GET"}, 405),
        ]:
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.request(**kwargs)["status"], expected)
        self.backend.submit_contact.assert_not_called()

    def test_errors_are_redacted(self):
        for exception, expected in ((backend.DiagnosticError("database_error", "private"), 503),
                                    (RuntimeError("private"), 500)):
            self.backend.submit_contact.side_effect = exception
            response = self.request()
            self.assertEqual(response["status"], expected)
            self.assertNotIn("private", json.dumps(response))


class DatabaseWriteTests(unittest.TestCase):
    def test_parameterized_insert_and_commit(self):
        subject = backend.Backend(mock.Mock())
        conn = mock.MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        values = {**VALID, "message": "'; DROP TABLE contact_submissions; --"}
        with mock.patch.object(subject, "connect", return_value=conn):
            subject.submit_contact(values)
        sql, params = cursor.execute.call_args.args
        self.assertNotIn(values["message"], sql)
        self.assertEqual(params, tuple(values[k] for k in ("name", "email", "subject", "message")))
        conn.commit.assert_called_once()
        conn.close.assert_called_once()

    def test_database_failure_does_not_leak(self):
        subject = backend.Backend(mock.Mock())
        conn = mock.MagicMock()
        conn.commit.side_effect = pymysql.OperationalError("secret-marker")
        with mock.patch.object(subject, "connect", return_value=conn):
            with self.assertRaises(backend.DiagnosticError) as caught:
                subject.submit_contact(VALID)
        self.assertNotIn("secret-marker", str(caught.exception))
        conn.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
