"""Secrets Manager and MySQL operations for the demo API and private diagnostics."""

import argparse
from contextlib import closing
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import ssl
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
import pymysql


class DiagnosticError(Exception):
    """An operator-safe error; never include upstream exception text."""

    def __init__(self, code, message, record_id=None):
        super().__init__(message)
        self.code = code
        self.record_id = record_id


@dataclass(frozen=True)
class Settings:
    region: str
    secret_id: str
    host: str
    database: str
    ca_bundle: str
    port: int = 3306

    @classmethod
    def from_env(cls, mapping=None):
        env = os.environ if mapping is None else mapping
        required = ("AWS_REGION", "DB_SECRET_ID", "DB_HOST", "DB_NAME", "DB_CA_BUNDLE")
        if any(not env.get(name, "").strip() for name in required):
            raise DiagnosticError(
                "configuration_error", "Set AWS_REGION, DB_SECRET_ID, DB_HOST, DB_NAME, and DB_CA_BUNDLE."
            )
        try:
            port = int(env.get("DB_PORT", "3306"))
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            raise DiagnosticError("configuration_error", "DB_PORT must be an integer from 1 to 65535.") from None
        ca_bundle = env["DB_CA_BUNDLE"].strip()
        try:
            with Path(ca_bundle).open("rb"):
                pass
        except OSError:
            raise DiagnosticError("configuration_error", "DB_CA_BUNDLE must be a readable CA certificate file.") from None
        return cls(
            region=env["AWS_REGION"].strip(),
            secret_id=env["DB_SECRET_ID"].strip(),
            host=env["DB_HOST"].strip(),
            database=env["DB_NAME"].strip(),
            ca_bundle=ca_bundle,
            port=port,
        )


class Backend:
    def __init__(self, settings):
        self.settings = settings

    def connect(self):
        """Fetch current credentials and open a verified TLS connection."""
        try:
            with closing(boto3.session.Session().client(
                "secretsmanager",
                region_name=self.settings.region,
                config=Config(connect_timeout=5, read_timeout=5,
                              retries={"mode": "standard", "total_max_attempts": 2}),
            )) as client:
                response = client.get_secret_value(
                    SecretId=self.settings.secret_id, VersionStage="AWSCURRENT"
                )
        except (BotoCoreError, ClientError):
            raise DiagnosticError(
                "secrets_error", "Cannot retrieve credentials; check the instance role, secret, KMS permissions, and HTTPS connectivity."
            ) from None
        try:
            secret = json.loads(response["SecretString"])
            if not isinstance(secret, dict) or any(
                not isinstance(secret.get(key), str) or not secret[key]
                for key in ("username", "password")
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise DiagnosticError(
                "secret_format_error", "The secret must contain JSON string fields username and password in SecretString."
            ) from None
        try:
            tls = ssl.create_default_context(cafile=self.settings.ca_bundle)
            tls.minimum_version = ssl.TLSVersion.TLSv1_2
            return pymysql.connect(
                host=self.settings.host,
                port=self.settings.port,
                database=self.settings.database,
                user=secret["username"],
                password=secret["password"],
                charset="utf8mb4",
                autocommit=False,
                connect_timeout=5,
                read_timeout=5,
                write_timeout=5,
                ssl=tls,
            )
        except (pymysql.MySQLError, OSError, ValueError):
            raise DiagnosticError(
                "database_connection_error", "Cannot connect to MySQL; check private DNS/network access, credentials, database, and the TLS CA/hostname."
            ) from None

    def health(self):
        """Read-only database readiness check; no diagnostic table required."""
        try:
            with closing(self.connect()) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    if cursor.fetchone() != (1,):
                        raise DiagnosticError("verification_error", "The MySQL health query returned an unexpected result.")
        except pymysql.MySQLError:
            raise DiagnosticError("database_query_error", "The MySQL health query failed.") from None
        return {"status": "ok", "database": "reachable"}

    def submit_contact(self, values):
        """Store a validated synthetic submission using the restricted runtime account."""
        try:
            with closing(self.connect()) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO contact_submissions (name, email, subject, message) VALUES (%s, %s, %s, %s)",
                        (values["name"], values["email"], values["subject"], values["message"]),
                    )
                connection.commit()
        except pymysql.MySQLError:
            raise DiagnosticError("database_query_error", "The submission could not be stored.") from None

    def probe(self):
        """Commit a synthetic record, verify its value, and clean up that record."""
        record_id = str(uuid.uuid4())
        payload = "probe:" + record_id
        try:
            with closing(self.connect()) as connection:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "INSERT INTO connectivity_probe (id, payload) VALUES (%s, %s)",
                            (record_id, payload),
                        )
                    connection.commit()
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT payload FROM connectivity_probe WHERE id = %s", (record_id,))
                        if cursor.fetchone() != (payload,):
                            raise DiagnosticError("verification_error", "The committed synthetic record could not be read back correctly.")
                finally:
                    # An INSERT commit can succeed even if its acknowledgement is lost.
                    # Attempt cleanup after every query failure, not only confirmed commits.
                    try:
                        connection.rollback()
                        with connection.cursor() as cursor:
                            cursor.execute("DELETE FROM connectivity_probe WHERE id = %s", (record_id,))
                        connection.commit()
                    except pymysql.MySQLError:
                        raise DiagnosticError(
                            "cleanup_error", "Cleanup could not be confirmed; a synthetic row may remain. Check record_id."
                        ) from None
        except DiagnosticError as error:
            error.record_id = record_id
            raise
        except pymysql.MySQLError:
            raise DiagnosticError(
                "database_query_error", "The probe query or commit failed; check the diagnostic table and SELECT/INSERT/DELETE grants.", record_id
            ) from None
        return {"status": "ok", "record_id": record_id, "cleaned_up": True}


def error_result(error):
    result = {"status": "error", "code": error.code, "message": str(error)}
    if error.record_id is not None:
        result["record_id"] = error.record_id
    return result


def run_check(backend, operation):
    try:
        return 200, getattr(backend, operation)()
    except DiagnosticError as error:
        return 503, error_result(error)
    except Exception:
        # Upstream errors can contain credentials, endpoints, or SQL values.
        return 500, {"status": "error", "code": "internal_error", "message": "An unexpected diagnostic failure occurred."}


def make_server(backend, port=8000):
    """Expose dependency health on loopback; mutations remain CLI-only."""
    class Handler(BaseHTTPRequestHandler):
        timeout = 5

        def respond(self, status, result):
            body = json.dumps(result).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/dependency-health":
                self.respond(*run_check(backend, "health"))
            else:
                self.respond(404, {"status": "error", "code": "not_found"})

        def do_POST(self):
            self.respond(405, {"status": "error", "code": "method_not_allowed"})

        def log_message(self, format, *args):
            pass

    return HTTPServer(("127.0.0.1", port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("health", "probe", "serve"))
    parser.add_argument("--port", type=int, default=8000, help="Loopback HTTP port for serve (default: 8000).")
    args = parser.parse_args(argv)
    try:
        backend = Backend(Settings.from_env())
        if args.operation == "serve":
            if not 1 <= args.port <= 65535:
                raise DiagnosticError("configuration_error", "The HTTP port must be from 1 to 65535.")
            try:
                with make_server(backend, args.port) as server:
                    print(json.dumps({"status": "listening", "address": "127.0.0.1", "port": args.port}), flush=True)
                    server.serve_forever()
            except KeyboardInterrupt:
                return 0
            except OSError:
                raise DiagnosticError("server_error", "Cannot open the loopback HTTP listener; check the port and local permissions.") from None
            return 0
        status, result = run_check(backend, args.operation)
        print(json.dumps(result))
        return 0 if status == 200 else 1
    except DiagnosticError as error:
        print(json.dumps(error_result(error)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
