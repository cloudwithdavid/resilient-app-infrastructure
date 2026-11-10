"""WSGI contact API; Apache serves the static site and proxies these routes."""
import json
import re
import logging
from threading import BoundedSemaphore
from http import HTTPStatus


from evidence.tests.fixture.backend import Backend, DiagnosticError, Settings

MAX_BODY = 16384
LIMITS = {"name": 80, "email": 254, "subject": 160, "message": 5000}
EMAIL = re.compile(r"^[^\s@]+@example\.(?:com|org|net)$", re.IGNORECASE)


def validate(payload):
    if not isinstance(payload, dict) or set(payload) - (set(LIMITS) | {"honeypot"}):
        return None
    if payload.get("honeypot", "") != "":
        return None
    values = {}
    for field, limit in LIMITS.items():
        value = payload.get(field)
        if not isinstance(value, str):
            return None
        value = value.strip()
        if not value or len(value) > limit or any((ord(c) < 32 and c not in "\n\r\t") or 0xD800 <= ord(c) <= 0xDFFF for c in value):
            return None
        values[field] = value
    if not EMAIL.fullmatch(values["email"]):
        return None
    return values


def create_app(backend=None):
    # Limit slow contact requests per worker, reserving threads for liveness.
    contact_slots = BoundedSemaphore(2)

    def application(environ, start_response):
        is_contact = (environ.get("PATH_INFO"), environ.get("REQUEST_METHOD")) == ("/api/contact", "POST")
        acquired = not is_contact or contact_slots.acquire(blocking=False)
        if not acquired:
            status, result = 503, {"status": "error", "code": "busy"}
        else:
            try:
                status, result = dispatch(environ, backend)
            finally:
                if is_contact:
                    contact_slots.release()
        body = json.dumps(result).encode("utf-8")
        start_response(f"{status} {HTTPStatus(status).phrase}", [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))), ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
        ])
        return [body]
    return application


def dispatch(environ, backend):
    path, method = environ.get("PATH_INFO", ""), environ.get("REQUEST_METHOD", "")
    if path not in ("/health", "/api/contact"):
        return 404, {"status": "error", "code": "not_found"}
    if (path, method) not in (("/health", "GET"), ("/api/contact", "POST")):
        return 405, {"status": "error", "code": "method_not_allowed"}
    if path == "/health":
        return 200, {"status": "ok"}
    values = None
    if path == "/api/contact":
        if environ.get("CONTENT_TYPE", "").split(";", 1)[0].strip().lower() != "application/json":
            return 415, {"status": "error", "code": "unsupported_media_type"}
        try:
            length = int(environ.get("CONTENT_LENGTH", ""))
            if length < 0:
                raise ValueError
        except ValueError:
            return 400, {"status": "error", "code": "invalid_request"}
        if length > MAX_BODY:
            return 413, {"status": "error", "code": "too_large"}
        try:
            raw = environ["wsgi.input"].read(length)
            if len(raw) != length:
                raise ValueError
            values = validate(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, ValueError, RecursionError):
            values = None
        if values is None:
            return 400, {"status": "error", "code": "invalid_submission"}
    try:
        subject = backend if backend is not None else Backend(Settings.from_env())
        subject.submit_contact(values)
        return 201, {"ok": True}
    except DiagnosticError as error:
        logging.warning("Contact API operational error: %s", error.code)
        return 503, {"status": "error", "code": "service_unavailable"}
    except Exception:
        logging.error("Contact API unexpected error")
        # Do not leak credentials, SQL, request data, or exception messages to clients.
        return 500, {"status": "error", "code": "internal_error"}


application = create_app()
