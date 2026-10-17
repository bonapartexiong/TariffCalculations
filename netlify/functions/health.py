"""Netlify Function adapter for GET /v1/health.

Thin adapter: ALL logic lives in ``backend.services.pipeline``.
Generic 500 on unexpected errors (no exception detail ever leaks).
"""

import json
import logging
import sys
import uuid
from pathlib import Path

log = logging.getLogger(__name__)


def _ensure_project_on_path():
    here = Path(__file__).resolve()
    for candidate in [here.parent, *here.parents]:
        if (candidate / "backend" / "services" / "pipeline.py").exists():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return
    root = here.parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


_ensure_project_on_path()

from backend.services import pipeline  # noqa: E402


def _response(status_code, payload):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "X-Request-ID": uuid.uuid4().hex,
        },
        "body": json.dumps(payload),
    }


def handler(event, context=None):
    try:
        method = (event or {}).get("httpMethod", "GET")
        if method != "GET":
            return _response(405, {"error": {"code": "method_not_allowed",
                                             "message": "Method not allowed."}})
        return _response(200, pipeline.health_info())
    except Exception:
        log.exception("Netlify health adapter failed")
        return _response(500, {"error": {"code": "internal_error",
                                         "message": "Internal error."}})
