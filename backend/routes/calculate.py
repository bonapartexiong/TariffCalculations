"""
Tariff calculation route.
"""

from flask import request, jsonify, g

from backend.config import API_VERSION, logger
from backend.exceptions import ValidationError, ProductMatchError, TariffCalculationError
from backend.validators import InputValidator


def calculate_duty(tariff_service, fee_calculator, calculation_logger):
    """Calculate customs duty endpoint (closure-style to receive services)."""

    def _handler():
        try:
            if not request.is_json:
                return jsonify({"error": "Request must be JSON"}), 400

            description, value = InputValidator.validate_calculation_input(
                request.json
            )

            # Match product
            try:
                match = tariff_service.find_match(description)
            except ProductMatchError as exc:
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

            # Compute fees
            fees = fee_calculator.calculate_fees(
                value=value,
                tariff_rate=match.tariff_rate,
                matched_description=match.description,
                confidence=match.confidence,
            )

            # Log to Supabase (fire-and-forget)
            if calculation_logger is not None:
                calculation_logger.log(description, value, g.request_id)
            else:
                logger.warning(
                    "Request %s: Supabase logging skipped (client unavailable)",
                    g.request_id,
                )

            response_data = {
                "matched_description": fees.matched_description,
                "confidence": round(fees.confidence, 3),
                "tariff": fees.tariff_rate,
                "duty": fees.duty,
                "merchandise_processing_fee": fees.merchandise_processing_fee,
                "harbor_maintenance_fee": fees.harbor_maintenance_fee,
                "subtotal": fees.subtotal,
                "footnote": (
                    "For precise tariff quotations and formal consultations, "
                    "reach out to Bo Xiong at BonaparteXiongBo@gmail.com."
                ),
                "request_id": g.request_id,
            }

            logger.info(
                "Request %s: Calculation successful (confidence: %.2f)",
                g.request_id,
                fees.confidence,
            )
            return jsonify(response_data), 200

        except ValidationError as exc:
            logger.warning(
                "Request %s: Validation error — %s", g.request_id, exc
            )
            return jsonify({"error": str(exc)}), 400

        except ProductMatchError as exc:
            logger.warning(
                "Request %s: Match error — %s", g.request_id, exc
            )
            return jsonify({"error": str(exc)}), 404

        except TariffCalculationError as exc:
            logger.error(
                "Request %s: Calculation error — %s", g.request_id, exc
            )
            return jsonify({"error": "Unable to calculate tariff"}), 500

        except Exception as exc:
            logger.exception(
                "Request %s: Unexpected error — %s", g.request_id, exc
            )
            return jsonify({"error": "Internal server error"}), 500

    return _handler
