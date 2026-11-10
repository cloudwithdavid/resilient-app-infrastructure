#!/usr/bin/env python3
"""Check a deployed app's liveness and synthetic contact write."""
import argparse
from http.client import HTTPException
import json
import socket
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def base_url(value):
    try:
        parts = urlsplit(value)
        if (value != value.strip() or any(ord(c) < 32 for c in value)
                or parts.scheme not in ("http", "https") or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.path not in ("", "/") or parts.query or parts.fragment):
            raise ValueError
        _ = parts.port  # Validate the port if present.
    except ValueError:
        raise argparse.ArgumentTypeError("Use an HTTP(S) origin such as https://example.com, without a path or credentials.") from None
    return value.rstrip("/")


def check(opener, url, method, expected_status, expected_field, expected_value, data=None):
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(request, timeout=45) as response:
            status = response.status
            body = response.read(65537)
    except HTTPError as error:
        error.close()
        hints = {
            400: "request was rejected",
            404: "endpoint not found; check the target and proxy routes",
            413: "request was too large",
            502: "proxy could not reach the application",
            503: "service unavailable; check application logs and dependency access",
        }
        hint = hints.get(error.code, "unexpected HTTP response")
        if 300 <= error.code < 400:
            hint = "redirect received; use the direct HTTPS application origin"
        return False, f"HTTP {error.code} ({hint})"
    except (URLError, OSError, HTTPException, ValueError, UnicodeError) as error:
        reason = error.reason if isinstance(error, URLError) else error
        if isinstance(reason, ssl.SSLCertVerificationError):
            return False, "TLS certificate verification failed; check the hostname and certificate"
        if isinstance(reason, socket.gaierror):
            return False, "DNS lookup failed; check the hostname and DNS access"
        if isinstance(reason, ConnectionRefusedError):
            return False, "connection refused; check that the application is listening"
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return False, "connection or response timed out; check application and network access"
        return False, "connection failed; check the target address and network access"
    if status != expected_status:
        return False, f"expected HTTP {expected_status}, received HTTP {status}"
    try:
        if len(body) > 65536:
            raise ValueError
        payload = json.loads(body)
        if (not isinstance(payload, dict) or type(payload.get(expected_field)) is not type(expected_value)
                or payload.get(expected_field) != expected_value):
            raise ValueError
    except (ValueError, UnicodeDecodeError, RecursionError):
        return False, f"unexpected response body; expected {expected_field}={expected_value!r}"
    return True, f"HTTP {status}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", type=base_url, help="Application origin, e.g. https://demo.example.com")
    args = parser.parse_args(argv)
    marker = f"smoke-test-{uuid4()}"
    payload = json.dumps({"name": "Smoke Test", "email": "smoke@example.com",
                          "subject": marker, "message": "Synthetic infrastructure smoke test."}).encode("utf-8")
    opener = build_opener(NoRedirect())
    health = check(opener, args.base_url + "/health", "GET", 200, "status", "ok")
    contact = check(opener, args.base_url + "/api/contact", "POST", 201, "ok", True, payload)
    for label, (passed, detail) in (("GET /health", health), ("POST /api/contact", contact)):
        print(f"{'PASS' if passed else 'FAIL'} {label}: {detail}")
    print(f"Synthetic submission subject: {marker}")
    passed = health[0] and contact[0]
    print("PASS smoke test" if passed else "FAIL smoke test")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
