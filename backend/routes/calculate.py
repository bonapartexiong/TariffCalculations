"""
Tariff calculation route.
"""

from flask import request, jsonify, g

from backend.config import API_VERSION, logger
from backend.exceptions import ValidationError, ProductMatchError, TariffCalculationError
from backend.services.pipeline import calculate_duty_pipeline


def calculate_duty(tariff_service, fee_calculator, calculation_logger):
    """Calculate customs duty endpoint (closure-style to receive services)."""

    def _handler():
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 400

        try:
            response_data = calculate_duty_pipeline(
                data=request.json,
                tariff_service=tariff_service,
                fee_calculator=fee_calculator,
                calculation_logger=calculation_logger,
                request_id=g.request_id,
            )
            logger.info(
                "Request %s: Calculation successful (confidence: %s)",
                g.request_id,
                response_data["confidence"],
            )
            return jsonify(response_data), 200

        except ValidationError as exc:
            logger.warning("Request %s: Validation error - %s", g.request_id, exc)
            return jsonify({"error": str(exc)}), 400

        except ProductMatchError as exc:
            logger.warning("Request %s: Match error - %s", g.request_id, exc)
            return (
                jsonify(
                    {
                        "error": str(exc),
                        "suggestion": (
                            "Try using more specific product details "
                            "(material, use, category)"
                        ),
                    }
                ),
                404,
            )

        except TariffCalculationError as exc:
            logger.error("Request %s: Calculation error - %s", g.request_id, exc)
            return jsonify({"error": "Unable to calculate tariff"}), 500

        except Exception as exc:
            logger.exception("Request %s: Unexpected error - %s", g.request_id, exc)
            return jsonify({"error": "Internal server error"}), 500

    return _handler
