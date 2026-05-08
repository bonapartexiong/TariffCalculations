"""
Request/response middleware: request-id tracking and security headers.
"""

import uuid

from flask import g, request

from backend.config import logger


def before_request():
    """Attach a unique request ID and log the incoming request."""
    g.request_id = str(uuid.uuid4())
    logger.info("Request %s: %s %s", g.request_id, request.method, request.path)


def after_request(response):
    """Inject security headers and the request ID into the response."""
    response.headers["X-Request-ID"] = g.get("request_id", "")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = (
        "max-age=31536000; includeSubDomains"
    )
    return response
