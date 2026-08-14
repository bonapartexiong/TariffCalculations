"""
Netlify Functions adapter for POST /v1/calculate.

Runs the exact same calculation pipeline as the Flask app, but as a serverless
function, so the frontend and backend can share a single free Netlify
deployment (no separate Railway/Docker bill). See netlify.toml for the
"/v1/*" -> function rewrites.
"""
import json
import os
import sys
import uuid

# Make the bundled "backend" package importable regardless of where Netlify
# places the function file inside the deployment bundle.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE), os.path.dirname(os.path.dirname(_HERE))):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from backend.config import logger
from backend.exceptions import ValidationError, ProductMatchError, TariffCalculationError
from backend.services.tariff_data import TariffDataService
from backend.services.fee_calculator import FeeCalculator
from backend.services.calculation_logger import CalculationLogger
from backend.services.pipeline import calculate_duty_pipeline

# Lazy module-level init; reused across warm invocations.
_SERVICES = None


def _get_services():
    global _SERVICES
    if _SERVICES is None:
        tariff_service = TariffDataService()
        tariff_service.load_data()
        _SERVICES = (tariff_service, FeeCalculator(), CalculationLogger())
    return _SERVICES


def _response(status_code, payload):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }


def handler(event, context):
    """Netlify function entry point (Lambda-style signature)."""
    request_id = str(uuid.uuid4())
    try:
        tariff_service, fee_calculator, calculation_logger = _get_services()
        body = json.loads(event.get("body") or "{}")
        payload = calculate_duty_pipeline(
            data=body,
            tariff_service=tariff_service,
            fee_calculator=fee_calculator,
            calculation_logger=calculation_logger,
            request_id=request_id,
        )
        return _response(200, payload)

    except ValidationError as exc:
        return _response(400, {"error": str(exc)})

    except ProductMatchError as exc:
        return _response(
            404,
            {
                "error": str(exc),
                "suggestion": (
                    "Try using more specific product details (material, use, category)"
                ),
            },
        )

    except TariffCalculationError as exc:
        logger.error("Request %s: Calculation error - %s", request_id, exc)
        return _response(500, {"error": "Unable to calculate tariff"})

    except Exception as exc:
        logger.exception("Request %s: Unexpected error - %s", request_id, exc)
        return _response(500, {"error": "Internal server error"})
