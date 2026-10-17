"""API error types for Module C.

Error responses carry only a stable ``code`` and a generic ``message``.
Internal details (tracebacks, file paths, upstream payloads) must never
reach the client; see ``backend/app.py`` and the Netlify adapters.
"""

from __future__ import annotations


class ApiError(Exception):
    """An error safe to render to the client."""

    status_code = 500
    code = "internal_error"
    default_message = "Internal error."

    def __init__(self, message=None, code=None, status_code=None):
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code

    def to_dict(self):
        return {"error": {"code": self.code, "message": self.message}}


class ValidationError(ApiError):
    status_code = 400
    code = "validation_error"
    default_message = "Request validation failed."


class NotFoundError(ApiError):
    status_code = 404
    code = "not_found"
    default_message = "Not found."


class MethodNotAllowedError(ApiError):
    status_code = 405
    code = "method_not_allowed"
    default_message = "Method not allowed."


class ServiceUnavailableError(ApiError):
    status_code = 503
    code = "service_unavailable"
    default_message = "Service temporarily unavailable."


class IndexUnavailableError(ServiceUnavailableError):
    code = "index_unavailable"
    default_message = "Tariff index is unavailable."
