"""Flask application factory for Module C.

``create_app(index_path=None)`` builds the app. No app object is created
at import time, and importing this module never requires Flask: if
Flask is missing, ``create_app`` raises a clear RuntimeError.
"""

from __future__ import annotations

import logging

try:
    from flask import Flask, jsonify
except Exception:  # pragma: no cover - Flask not installed.
    Flask = None  # type: ignore
    jsonify = None  # type: ignore

from backend.config import get_settings
from backend.exceptions import ApiError
from backend.middleware import register_middleware

log = logging.getLogger(__name__)


def create_app(index_path=None, settings=None):
    """Build and return a Flask app. No global app is created at import."""
    if Flask is None:
        raise RuntimeError(
            "Flask is not installed. Install backend/requirements.txt to run the API."
        )
    settings = settings or get_settings()
    app = Flask(__name__)
    app.config["INDEX_PATH"] = index_path or settings.index_path
    app.config["JSON_SORT_KEYS"] = False

    # CORS: origins from env, already stripped in config. No wildcard
    # credentials; when no origins are configured, no CORS headers.
    try:
        from flask_cors import CORS

        if settings.allowed_origins:
            CORS(app, resources={r"/v1/*": {"origins": list(settings.allowed_origins)}})
    except Exception as exc:
        log.debug("flask-cors unavailable, CORS disabled: %s", exc)

    register_middleware(app)

    from backend.routes.calculate import calculate_bp
    from backend.routes.health import health_bp
    from backend.routes.rag import rag_bp

    for blueprint in (calculate_bp, health_bp, rag_bp):
        if blueprint is not None:
            app.register_blueprint(blueprint)

    @app.errorhandler(ApiError)
    def _handle_api_error(error):
        return jsonify(error.to_dict()), error.status_code

    @app.errorhandler(400)
    def _handle_bad_request(error):
        # Malformed JSON / routing-level 400s: generic message, no detail.
        return jsonify({"error": {"code": "validation_error",
                                  "message": "Request validation failed."}}), 400

    @app.errorhandler(404)
    def _handle_not_found(error):
        return jsonify({"error": {"code": "not_found",
                                  "message": "Not found."}}), 404

    @app.errorhandler(405)
    def _handle_method_not_allowed(error):
        return jsonify({"error": {"code": "method_not_allowed",
                                  "message": "Method not allowed."}}), 405

    @app.errorhandler(Exception)
    def _handle_unexpected(error):
        # Never leak exception detail (traceback / paths / payloads).
        log.exception("Unhandled error")
        return jsonify({"error": {"code": "internal_error",
                                  "message": "Internal error."}}), 500

    # Expose the resolved index path for tests / operators.
    app.config["SETTINGS"] = settings
    return app


if __name__ == "__main__":  # Local dev only; production uses gunicorn.
    _app = create_app()
    _app.run(host="127.0.0.1", port=get_settings().port)
