"""
Health-check route.
"""

from datetime import datetime, timezone

from flask import jsonify

from backend.config import API_VERSION, logger


def health_check(tariff_service, supabase_client):
    """Comprehensive health check endpoint (closure-style registration)."""

    def _handler():
        try:
            health = {
                "status": "healthy",
                "version": API_VERSION,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "checks": {
                    "tariff_data": tariff_service.df is not None,
                    "ml_model": tariff_service.tfidf_matrix is not None,
                    "supabase": False,
                },
            }

            # Probe Supabase
            if supabase_client is not None:
                try:
                    supabase_client.table("Calculations").select("*").limit(1).execute()
                    health["checks"]["supabase"] = True
                except Exception as exc:
                    logger.error("Supabase health check failed: %s", exc)

            all_healthy = all(health["checks"].values())
            health["status"] = "healthy" if all_healthy else "degraded"
            return jsonify(health), 200 if all_healthy else 503

        except Exception as exc:
            logger.exception("Health check error: %s", exc)
            return jsonify({"status": "unhealthy", "error": str(exc)}), 503

    return _handler
