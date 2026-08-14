"""
Health-check route.
"""
from datetime import datetime, timezone

from flask import jsonify

from backend.config import API_VERSION, logger


def health_check(tariff_service, calculation_logger):
    """Comprehensive health check endpoint (closure-style registration)."""

    def _handler():
        try:
            records_loaded = bool(getattr(tariff_service, "records", None))
            matcher = getattr(tariff_service, "matcher", None)
            provider = getattr(tariff_service, "provider", None)
            llm_client = getattr(tariff_service, "llm_client", None)

            checks = {
                "tariff_data": records_loaded,
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

            matching = {
                "engine": engine,
                "provider": engine_provider,
                "model": engine_model,
                "records": len(getattr(tariff_service, "records", [])),
            }

            logging_backend = (
                calculation_logger.backend
                if calculation_logger is not None
                else "none"
            )

            health = {
                "status": "healthy" if all(checks.values()) else "degraded",
                "version": API_VERSION,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "checks": checks,
                "matching": matching,
                "logging": logging_backend,
            }

            return jsonify(health), 200 if all(checks.values()) else 503

        except Exception as exc:
            logger.exception("Health check error: %s", exc)
            return jsonify({"status": "unhealthy", "error": str(exc)}), 503

    return _handler
