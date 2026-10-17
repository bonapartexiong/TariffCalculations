"""Netlify Function adapter for POST /v1/calculate.

Thin adapter: ALL logic lives in ``backend.services.pipeline``. This file
only translates the Netlify event shape <-> the pipeline and renders a
generic 500 on unexpected errors (no exception detail ever leaks).
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
    # Fallback: assume repo root is three levels up (netlify/functions/).
    root = here.parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


_ensure_project_on_path()

from backend.exceptions import ApiError  # noqa: E402
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


def _generic_500():
    return _response(500, {"error": {"code": "internal_error", "message": "Internal error."}})


def handler(event, context=None):
    try:
        method = (event or {}).get("httpMethod", "POST")
        if method != "POST":
            return _response(405, {"error": {"code": "method_not_allowed",
                                             "message": "Method not allowed."}})
        raw_body = (event or {}).get("body") or "{}"
        try:
            payload = json.loads(raw_body)
        except (TypeError, ValueError):
            return _response(400, {"error": {"code": "validation_error",
                                             "message": "Request validation failed."}})
        if not isinstance(payload, dict):
            return _response(400, {"error": {"code": "validation_error",
                                             "message": "Request validation failed."}})
        result = pipeline.run_calculate(payload)
        return _response(200, result)
    except ApiError as exc:
        return _response(exc.status_code, exc.to_dict())
    except Exception:
        log.exception("Netlify calculate adapter failed")
        return _generic_500()
