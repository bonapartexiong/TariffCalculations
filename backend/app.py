"""
Tariff Calculation API — application factory.
"""

import os
import sys
from typing import Optional

# Make the repository root importable so the `backend.*` imports below work no
# matter where the app is started from:
#   - repo root:      gunicorn backend.app:app
#   - backend/ dir:   gunicorn app:app  (Render rootDir=backend, Docker CMD)
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from flask import Flask, jsonify
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv

from backend.config import (
    API_VERSION,
    RATE_LIMIT_HOURLY,
    RATE_LIMIT_MINUTELY,
    RATE_LIMIT_CALCULATE,
    MAX_CONTENT_LENGTH,
    logger,
)
from backend.middleware import before_request, after_request
from backend.services.tariff_data import TariffDataService
from backend.services.fee_calculator import FeeCalculator
from backend.services.calculation_logger import CalculationLogger
from backend.routes.health import health_check
from backend.routes.calculate import calculate_duty

load_dotenv()


def _init_supabase():
    """Attempt to initialize Supabase. Returns None on failure (never crashes).

    Supabase is optional; the supabase package is imported lazily so the
    default (free) deployment has no database dependency at all.
    """
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")

    if not url or not key:
        return None

    if not url.startswith("https://"):
        logger.error("SUPABASE_URL must be an HTTPS URL")
        return None

    try:
        from supabase import create_client
    except ImportError:
        logger.warning(
            "SUPABASE_URL/KEY set but the 'supabase' package is not installed; "
            "install it to enable Supabase logging",
        )
        return None

    try:
        client = create_client(supabase_url=url, supabase_key=key)
        client.table("Calculations").select("*").limit(1).execute()
        logger.info("Supabase connection established")
        return client
    except Exception as exc:
        logger.error("Supabase initialization failed: %s", exc)
        logger.warning("Continuing without Supabase — logging will use another backend")
        return None


def _init_calculation_logger(supabase_client) -> CalculationLogger:
    """Build the calculation logger from environment configuration."""
    return CalculationLogger(supabase_client=supabase_client)


def create_app() -> Flask:
    """Build and configure the Flask application."""
    app = Flask(__name__)

    # --- Trust proxy headers for correct IP behind CDN/load-balancer ---
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    # --- Request size limit (prevents memory-exhaustion attacks) ---
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

    # --- CORS ---
    allowed_origins = os.getenv(
        "ALLOWED_ORIGINS", "http://localhost:3000"
    ).split(",")
    CORS(app, origins=allowed_origins)

    # --- Rate limiter (in-memory storage; use Redis for multi-worker deploys) ---
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=[RATE_LIMIT_HOURLY, RATE_LIMIT_MINUTELY],
        storage_uri="memory://",
    )

    # --- Railway-specific ---
    if os.getenv("RAILWAY_ENVIRONMENT"):
        app.config["PROPAGATE_EXCEPTIONS"] = True
        logger.info("Running in Railway environment")

    # --- Lazy service initialization ---
    supabase_client = _init_supabase()

    tariff_service = TariffDataService()
    try:
        tariff_service.load_data()
    except Exception:
        logger.critical(
            "Failed to load tariff data — app will start but calculations will fail"
        )

    fee_calculator = FeeCalculator()
    calculation_logger = _init_calculation_logger(supabase_client)

    # --- Middleware ---
    app.before_request(before_request)
    app.after_request(after_request)

    # --- Routes ---
    app.add_url_rule(
        f"/{API_VERSION}/health",
        "health_check",
        health_check(tariff_service, calculation_logger),
        methods=["GET"],
    )

    calc_handler = calculate_duty(tariff_service, fee_calculator, calculation_logger)
    calc_decorated = limiter.limit(RATE_LIMIT_CALCULATE)(calc_handler)
    app.add_url_rule(
        f"/{API_VERSION}/calculate",
        "calculate_duty",
        calc_decorated,
        methods=["POST"],
    )

    # --- Error handlers ---
    @app.errorhandler(429)
    def ratelimit_handler(_e):
        return (
            jsonify(
                {
                    "error": "Rate limit exceeded",
                    "message": "Too many requests. Please try again later.",
                }
            ),
            429,
        )

    @app.errorhandler(404)
    def not_found(_e):
        return jsonify({"error": "Endpoint not found"}), 404

    @app.errorhandler(413)
    def too_large(_e):
        return (
            jsonify({"error": "Request body too large (max 1 MB)"}),
            413,
        )

    @app.errorhandler(500)
    def internal_error(_e):
        logger.exception("Internal server error")
        return jsonify({"error": "Internal server error"}), 500

    logger.info("Application factory complete")
    return app


# Module-level app for Gunicorn (`gunicorn app:app`)
app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "False").lower() == "true"

    if debug:
        logger.warning("Running in DEBUG mode — DO NOT use in production!")

    app.run(host="0.0.0.0", port=port, debug=debug)
