"""Offline checks for the private RDS connectivity diagnostic backend."""

import contextlib
import http.client
import io
import json
from pathlib import Path
import ssl
import tempfile
import threading
import unittest
from unittest import mock
import uuid

from botocore.exceptions import ClientError
import pymysql

from app import backend


class BackendTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.ca_bundle = Path(self.tempdir.name) / "rds-ca.pem"
        self.ca_bundle.write_text("test certificate fixture", encoding="utf-8")
        self.environment = {
            "AWS_REGION": "us-east-1",
            "DB_SECRET_ID": "portfolio/rds",
            "DB_HOST": "example.private.rds.amazonaws.com",
            "DB_NAME": "portfolio_diagnostics",
            "DB_CA_BUNDLE": str(self.ca_bundle),
        }
        self.settings = backend.Settings.from_env(self.environment)
        self.subject = backend.Backend(self.settings)

    def connection(self):
        connection = mock.MagicMock(name="mysql_connection")
        connection.__enter__.return_value = connection
        cursor = connection.cursor.return_value
        cursor.__enter__.return_value = cursor
        return connection, cursor


class SettingsTests(BackendTestCase):
    def test_required_configuration_and_default_port(self):
        self.assertEqual(self.settings.port, 3306)
        self.assertEqual(self.settings.region, self.environment["AWS_REGION"])
        self.assertEqual(self.settings.host, self.environment["DB_HOST"])
        self.assertEqual(self.settings.database, self.environment["DB_NAME"])
        self.assertEqual(self.settings.secret_id, self.environment["DB_SECRET_ID"])
        self.assertEqual(str(self.settings.ca_bundle), str(self.ca_bundle))

    def test_missing_or_blank_required_configuration_is_rejected(self):
        for name in self.environment:
            for value in (None, "", "   "):
                with self.subTest(name=name, value=value):
                    environment = dict(self.environment)
                    if value is None:
                        del environment[name]
                    else:
                        environment[name] = value
                    with self.assertRaises(backend.DiagnosticError) as caught:
                        backend.Settings.from_env(environment)
                    self.assertEqual(caught.exception.code, "configuration_error")

    def test_invalid_port_is_rejected(self):
        for value in ("0", "65536", "-1", "not-a-port", "3306.5", ""):
            with self.subTest(port=value):
                environment = {**self.environment, "DB_PORT": value}
                with self.assertRaises(backend.DiagnosticError):
                    backend.Settings.from_env(environment)

    def test_explicit_valid_port(self):
        settings = backend.Settings.from_env({**self.environment, "DB_PORT": "3307"})
        self.assertEqual(settings.port, 3307)

    def test_ca_bundle_must_be_an_existing_file(self):
        for path in (Path(self.tempdir.name), self.ca_bundle.with_name("missing.pem")):
            with self.subTest(path=path):
                with self.assertRaises(backend.DiagnosticError):
                    backend.Settings.from_env({**self.environment, "DB_CA_BUNDLE": str(path)})

    def test_unreadable_ca_bundle_is_rejected(self):
        with mock.patch.object(Path, "open", side_effect=PermissionError("test denial")):
            with self.assertRaises(backend.DiagnosticError) as caught:
                backend.Settings.from_env(self.environment)
        self.assertEqual(caught.exception.code, "configuration_error")


class ConnectionTests(BackendTestCase):
    def secret_client(self, payload):
        client = mock.MagicMock(name="secrets_manager")
        client.get_secret_value.return_value = payload
        return client

    def test_iam_secret_lookup_and_verified_tls_connection(self):
        secret = self.secret_client({"SecretString": json.dumps({
            "username": "synthetic_user",
            "password": "synthetic_password",
            "host": "untrusted.example",
            "dbname": "untrusted_database",
            "port": 1234,
        })})
        tls_context = ssl.create_default_context()
        with mock.patch.object(backend.ssl, "create_default_context", return_value=tls_context) as context_factory:
            with mock.patch.object(backend.boto3.session.Session, "client", return_value=secret) as client_factory:
                with mock.patch.object(backend.pymysql, "connect") as connect:
                    result = self.subject.connect()

        self.assertIs(result, connect.return_value)
        factory_args = client_factory.call_args
        self.assertEqual(factory_args.args, ("secretsmanager",))
        self.assertEqual(factory_args.kwargs["region_name"], self.settings.region)
        self.assertNotIn("aws_access_key_id", factory_args.kwargs)
        self.assertNotIn("aws_secret_access_key", factory_args.kwargs)
        config = factory_args.kwargs["config"]
        self.assertGreater(config.connect_timeout, 0)
        self.assertLessEqual(config.connect_timeout, 5)
        self.assertGreater(config.read_timeout, 0)
        self.assertLessEqual(config.read_timeout, 5)
        self.assertIn("retries", config.__dict__)
        secret.get_secret_value.assert_called_once_with(
            SecretId=self.settings.secret_id, VersionStage="AWSCURRENT"
        )
        secret.close.assert_called_once_with()
        options = connect.call_args.kwargs
        self.assertEqual(options["host"], self.settings.host)
        self.assertEqual(options["database"], self.settings.database)
        self.assertEqual(options["port"], self.settings.port)
        self.assertEqual(options["user"], "synthetic_user")
        self.assertEqual(options["password"], "synthetic_password")
        context_factory.assert_called_once_with(cafile=str(self.ca_bundle))
        self.assertIs(options["ssl"], tls_context)
        self.assertIs(tls_context.check_hostname, True)
        self.assertEqual(tls_context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(tls_context.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertIs(options["autocommit"], False)
        self.assertEqual(options["charset"], "utf8mb4")
        for timeout in ("connect_timeout", "read_timeout", "write_timeout"):
            self.assertGreater(options[timeout], 0)
            self.assertLessEqual(options[timeout], 5)

    def test_invalid_secret_is_rejected_without_connecting(self):
        payloads = [
            {}, {"SecretBinary": b"not-supported"}, {"SecretString": "not-json"},
            {"SecretString": "[]"}, {"SecretString": "null"},
            {"SecretString": "{}"},
            {"SecretString": json.dumps({"username": "user", "password": ""})},
            {"SecretString": json.dumps({"username": 1, "password": "password"})},
            {"SecretString": json.dumps({"username": "user", "password": None})},
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                secret = self.secret_client(payload)
                with mock.patch.object(backend.boto3.session.Session, "client", return_value=secret):
                    with mock.patch.object(backend.pymysql, "connect") as connect:
                        with self.assertRaises(backend.DiagnosticError):
                            self.subject.connect()
                connect.assert_not_called()
                secret.close.assert_called_once_with()

    def test_aws_failure_is_redacted_and_client_is_closed(self):
        secret = self.secret_client({})
        secret.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "secret-password-marker"}},
            "GetSecretValue",
        )
        with mock.patch.object(backend.boto3.session.Session, "client", return_value=secret):
            with mock.patch.object(backend.pymysql, "connect") as connect:
                with self.assertRaises(backend.DiagnosticError) as caught:
                    self.subject.connect()
        self.assertNotIn("secret-password-marker", str(caught.exception))
        connect.assert_not_called()
        secret.close.assert_called_once_with()

    def test_mysql_connection_failure_is_redacted(self):
        secret = self.secret_client({"SecretString": json.dumps({
            "username": "synthetic_user", "password": "secret-password-marker",
        })})
        tls_context = ssl.create_default_context()
        with mock.patch.object(backend.ssl, "create_default_context", return_value=tls_context):
            with mock.patch.object(backend.boto3.session.Session, "client", return_value=secret):
                with mock.patch.object(backend.pymysql, "connect", side_effect=pymysql.OperationalError(
                    1045, "Access denied: secret-password-marker"
                )) as connect:
                    with self.assertRaises(backend.DiagnosticError) as caught:
                        self.subject.connect()
        self.assertNotIn("secret-password-marker", str(caught.exception))
        self.assertEqual(connect.call_count, 1)
        secret.close.assert_called_once_with()

    def test_pinned_driver_rejects_server_without_tls_before_sending_credentials(self):
        # Exercise the real driver's handshake decision without creating a socket.
        # This protects against silently falling back to plaintext after an upgrade.
        tls_context = ssl.create_default_context()
        tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
        connection = pymysql.connections.Connection(
            user="synthetic_user", password="synthetic_password",
            ssl=tls_context, defer_connect=True,
        )
        connection.server_version = "8.0.0"
        connection.server_capabilities = 0
        with mock.patch.object(connection, "write_packet") as write_packet:
            with self.assertRaises(pymysql.OperationalError) as caught:
                connection._request_authentication()
        self.assertEqual(caught.exception.args[0], 2026)
        write_packet.assert_not_called()

    def test_real_driver_rejects_invalid_ca_before_network_connection(self):
        secret = self.secret_client({"SecretString": json.dumps({
            "username": "synthetic_user", "password": "synthetic_password",
        })})
        with mock.patch.object(backend.boto3.session.Session, "client", return_value=secret):
            with mock.patch.object(pymysql.connections.socket, "create_connection") as create_connection:
                with self.assertRaises(backend.DiagnosticError) as caught:
                    self.subject.connect()
        self.assertEqual(caught.exception.code, "database_connection_error")
        create_connection.assert_not_called()
        secret.close.assert_called_once_with()


class DiagnosticTests(BackendTestCase):
    def test_health_uses_only_select_and_closes_connection(self):
        connection, cursor = self.connection()
        cursor.fetchone.return_value = (1,)
        with mock.patch.object(self.subject, "connect", return_value=connection):
            result = self.subject.health()
        self.assertEqual(result, {"status": "ok", "database": "reachable"})
        cursor.execute.assert_called_once_with("SELECT 1")
        connection.commit.assert_not_called()
        connection.close.assert_called_once_with()

    def test_health_query_failure_is_redacted_and_connection_closes(self):
        connection, cursor = self.connection()
        cursor.execute.side_effect = pymysql.OperationalError(2006, "secret-password-marker")
        with mock.patch.object(self.subject, "connect", return_value=connection):
            with self.assertRaises(backend.DiagnosticError) as caught:
                self.subject.health()
        self.assertNotIn("secret-password-marker", str(caught.exception))
        connection.close.assert_called_once_with()

    def test_health_unexpected_result_is_failure(self):
        connection, cursor = self.connection()
        cursor.fetchone.return_value = None
        with mock.patch.object(self.subject, "connect", return_value=connection):
            with self.assertRaises(backend.DiagnosticError) as caught:
                self.subject.health()
        self.assertEqual(caught.exception.code, "verification_error")
        connection.close.assert_called_once_with()

    def test_probe_commits_reads_and_deletes_only_its_synthetic_record(self):
        connection, cursor = self.connection()

        def inserted_payload():
            insert = next(call for call in cursor.execute.call_args_list
                          if call.args[0].lstrip().upper().startswith("INSERT"))
            return (insert.args[1][1],)

        cursor.fetchone.side_effect = inserted_payload
        with mock.patch.object(self.subject, "connect", return_value=connection):
            result = self.subject.probe()
        self.assertEqual(result["status"], "ok")
        self.assertIs(result["cleaned_up"], True)
        self.assertEqual(str(uuid.UUID(result["record_id"])), result["record_id"])
        queries = cursor.execute.call_args_list
        self.assertEqual(len(queries), 3)
        self.assertTrue(queries[0].args[0].lstrip().upper().startswith("INSERT"))
        self.assertTrue(queries[1].args[0].lstrip().upper().startswith("SELECT"))
        self.assertTrue(queries[2].args[0].lstrip().upper().startswith("DELETE"))
        for query in queries:
            self.assertIn("connectivity_probe", query.args[0])
            self.assertIn("%s", query.args[0])
            self.assertNotIn(result["record_id"], query.args[0])
            self.assertEqual(query.args[1][0], result["record_id"])
            self.assertNotIn("CREATE", query.args[0].upper())
        self.assertEqual(connection.commit.call_count, 2)
        connection.rollback.assert_called_once_with()
        connection.close.assert_called_once_with()
        methods = [call[0] for call in connection.mock_calls]
        select_index = next(index for index, call in enumerate(connection.mock_calls)
                            if call[0] == "cursor().execute"
                            and call.args[0].lstrip().upper().startswith("SELECT"))
        self.assertLess(methods.index("commit"), select_index)

    def test_probe_read_mismatch_still_cleans_up_committed_record(self):
        connection, cursor = self.connection()
        cursor.fetchone.return_value = ("unexpected payload",)
        with mock.patch.object(self.subject, "connect", return_value=connection):
            with self.assertRaises(backend.DiagnosticError):
                self.subject.probe()
        queries = cursor.execute.call_args_list
        self.assertTrue(queries[-1].args[0].lstrip().upper().startswith("DELETE"))
        self.assertEqual(queries[0].args[1][0], queries[-1].args[1][0])
        self.assertEqual(connection.commit.call_count, 2)
        connection.close.assert_called_once_with()

    def test_probe_query_failure_still_attempts_cleanup(self):
        connection, cursor = self.connection()

        def execute(sql, parameters=None):
            if sql.lstrip().upper().startswith("SELECT"):
                raise pymysql.OperationalError(2006, "secret-password-marker")

        cursor.execute.side_effect = execute
        with mock.patch.object(self.subject, "connect", return_value=connection):
            with self.assertRaises(backend.DiagnosticError) as caught:
                self.subject.probe()
        self.assertNotIn("secret-password-marker", str(caught.exception))
        queries = cursor.execute.call_args_list
        self.assertTrue(queries[-1].args[0].lstrip().upper().startswith("DELETE"))
        connection.rollback.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_probe_lost_insert_commit_acknowledgement_still_attempts_cleanup(self):
        connection, cursor = self.connection()
        connection.commit.side_effect = [
            pymysql.OperationalError(2013, "secret-password-marker"), None
        ]
        with mock.patch.object(self.subject, "connect", return_value=connection):
            with self.assertRaises(backend.DiagnosticError) as caught:
                self.subject.probe()
        self.assertNotIn("secret-password-marker", str(caught.exception))
        queries = cursor.execute.call_args_list
        self.assertEqual(len(queries), 2)
        self.assertTrue(queries[0].args[0].lstrip().upper().startswith("INSERT"))
        self.assertTrue(queries[1].args[0].lstrip().upper().startswith("DELETE"))
        self.assertEqual(queries[0].args[1][0], queries[1].args[1][0])
        self.assertEqual(connection.commit.call_count, 2)
        connection.rollback.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_probe_cleanup_failure_reports_nonsecret_record_id(self):
        connection, cursor = self.connection()

        def execute(sql, parameters=None):
            if sql.lstrip().upper().startswith("DELETE"):
                raise pymysql.OperationalError(2006, "secret-password-marker")

        def inserted_payload():
            return (cursor.execute.call_args_list[0].args[1][1],)

        cursor.execute.side_effect = execute
        cursor.fetchone.side_effect = inserted_payload
        with mock.patch.object(self.subject, "connect", return_value=connection):
            with self.assertRaises(backend.DiagnosticError) as caught:
                self.subject.probe()
        self.assertNotIn("secret-password-marker", str(caught.exception))
        self.assertEqual(caught.exception.record_id, cursor.execute.call_args_list[0].args[1][0])
        self.assertEqual(str(uuid.UUID(caught.exception.record_id)), caught.exception.record_id)
        connection.close.assert_called_once_with()


class InterfaceTests(BackendTestCase):
    def test_known_failure_is_service_unavailable_json(self):
        self.subject.health = mock.Mock(side_effect=backend.DiagnosticError(
            "database_unavailable", "Database is unavailable."
        ))
        status, payload = backend.run_check(self.subject, "health")
        self.assertEqual(status, 503)
        self.assertEqual(payload, {
            "status": "error", "code": "database_unavailable", "message": "Database is unavailable."
        })

    def test_unexpected_failure_is_redacted(self):
        self.subject.health = mock.Mock(side_effect=RuntimeError("secret-password-marker"))
        status, payload = backend.run_check(self.subject, "health")
        self.assertEqual(status, 500)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["code"], "internal_error")
        self.assertNotIn("secret-password-marker", json.dumps(payload))

    def test_cleanup_failure_record_id_is_in_failure_json(self):
        record_id = str(uuid.uuid4())
        self.subject.probe = mock.Mock(side_effect=backend.DiagnosticError(
            "cleanup_failed", "Synthetic record cleanup failed.", record_id=record_id
        ))
        status, payload = backend.run_check(self.subject, "probe")
        self.assertEqual(status, 503)
        self.assertEqual(payload["record_id"], record_id)

    def test_cli_success_and_failure_exit_codes(self):
        for operation in ("health", "probe"):
            for status, payload, expected_exit in (
                (200, {"status": "ok"}, 0),
                (503, {"status": "error", "code": "database_unavailable"}, 1),
            ):
                with self.subTest(operation=operation, status=status):
                    output = io.StringIO()
                    with mock.patch.object(backend.Settings, "from_env", return_value=self.settings):
                        with mock.patch.object(backend, "Backend", return_value=self.subject):
                            with mock.patch.object(backend, "run_check", return_value=(status, payload)) as check:
                                with contextlib.redirect_stdout(output):
                                    exit_code = backend.main([operation])
                    self.assertEqual(exit_code, expected_exit)
                    self.assertEqual(json.loads(output.getvalue()), payload)
                    check.assert_called_once_with(self.subject, operation)

    def test_cli_configuration_failure_is_json_and_nonzero(self):
        output = io.StringIO()
        error = backend.DiagnosticError("configuration_error", "Missing required configuration.")
        with mock.patch.object(backend.Settings, "from_env", side_effect=error):
            with contextlib.redirect_stdout(output):
                exit_code = backend.main(["health"])
        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(output.getvalue())["code"], "configuration_error")

    def test_http_is_loopback_health_only_and_suppresses_access_logs(self):
        self.subject.health = mock.Mock(return_value={"status": "ok", "database": "reachable"})
        self.subject.probe = mock.Mock()
        server = backend.make_server(self.subject, port=0)
        self.assertEqual(server.server_address[0], "127.0.0.1")
        self.assertEqual(server.RequestHandlerClass.timeout, 5)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.addCleanup(cleanup)
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            for method, path, expected_status in (
                ("GET", "/dependency-health", 200), ("GET", "/probe", 404),
                ("GET", "/", 404), ("POST", "/dependency-health", 405), ("POST", "/probe", 405),
            ):
                with self.subTest(method=method, path=path):
                    request = http.client.HTTPConnection(*server.server_address, timeout=5)
                    try:
                        request.request(method, path)
                        response = request.getresponse()
                        payload = json.loads(response.read())
                        self.assertEqual(response.status, expected_status)
                        self.assertEqual(response.getheader("Content-Type"), "application/json")
                        self.assertEqual(response.getheader("Cache-Control"), "no-store")
                        self.assertIn("status", payload)
                    finally:
                        request.close()
            self.subject.health.side_effect = backend.DiagnosticError(
                "database_unavailable", "Database is unavailable."
            )
            request = http.client.HTTPConnection(*server.server_address, timeout=5)
            try:
                request.request("GET", "/dependency-health")
                response = request.getresponse()
                self.assertEqual(response.status, 503)
                self.assertEqual(json.loads(response.read())["code"], "database_unavailable")
            finally:
                request.close()
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(self.subject.health.call_args_list, [mock.call(), mock.call()])
        self.subject.probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
