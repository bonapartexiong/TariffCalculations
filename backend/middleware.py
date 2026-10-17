"""Request-id + security-headers middleware for Module C.

* Every request gets an ``X-Request-ID`` (client-supplied if present,
  otherwise a fresh UUID). The same value is echoed on the response.
* Every response carries a small set of security headers.
* Importing this module must not require Flask.
"""

from __future__ import annotations

import uuid

try:  # Flask is optional at import time.
    from flask import g, request
except Exception:  # pragma: no cover - Flask not installed.
    g = None  # type: ignore
    request = None  # type: ignore

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def new_request_id():
    return uuid.uuid4().hex


def register_middleware(app):
    """Attach request-id + security-header hooks to a Flask app."""
    if g is None:
        raise RuntimeError("Flask is required to register middleware.")

    @app.before_request
    def _assign_request_id():
        incoming = request.headers.get("X-Request-ID")
        g.request_id = incoming.strip() if incoming and incoming.strip() else new_request_id()

    @app.after_request
    def _attach_headers(response):
        request_id = getattr(g, "request_id", None) or new_request_id()
        response.headers["X-Request-ID"] = request_id
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response

    return app
