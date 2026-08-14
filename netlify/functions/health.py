"""
Netlify Functions adapter for GET /v1/health.
"""
import json
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE), os.path.dirname(os.path.dirname(_HERE))):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from backend.config import API_VERSION
from backend.services.tariff_data import TariffDataService
from backend.services.fee_calculator import FeeCalculator
from backend.services.calculation_logger import CalculationLogger

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
    try:
        tariff_service, _, calculation_logger = _get_services()
        matcher = getattr(tariff_service, "matcher", None)
        provider = getattr(tariff_service, "provider", None)
        llm_client = getattr(tariff_service, "llm_client", None)

        checks = {
            "tariff_data": bool(getattr(tariff_service, "records", None)),
            "matcher": matcher is not None,
        }

        if llm_client is not None:
            engine, engine_provider, engine_model = (
                "llm",
                llm_client.name,
                llm_client.model,
            )
        elif provider is not None:
            engine, engine_provider, engine_model = (
                "llm_embeddings",
                provider.name,
                getattr(matcher, "embedding_model", None) or provider.model,
            )
        else:
            engine, engine_provider, engine_model = "lexical_tfidf", "none", None

        payload = {
            "status": "healthy" if all(checks.values()) else "degraded",
            "version": API_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": checks,
            "matching": {
                "engine": engine,
                "provider": engine_provider,
                "model": engine_model,
                "records": len(getattr(tariff_service, "records", [])),
            },
            "logging": calculation_logger.backend,
        }
        return _response(200 if all(checks.values()) else 503, payload)

    except Exception as exc:
        return _response(503, {"status": "unhealthy", "error": str(exc)})
