"""
Shared tariff-calculation pipeline used by both the Flask routes and the
serverless (Netlify Functions) adapter. Keeps behaviour identical across
deployment targets.
"""
from __future__ import annotations

from backend.validators import InputValidator

FOOTNOTE = (
    "For precise tariff quotations and formal consultations, reach out to "
    "Bo Xiong at BonaparteXiongBo@gmail.com."
)


def calculate_duty_pipeline(
    data: dict,
    tariff_service,
    fee_calculator,
    calculation_logger,
    request_id: str,
) -> dict:
    """Validate input, match the product, compute fees and return the payload.

    Raises ValidationError / ProductMatchError / TariffCalculationError which
    callers map to the appropriate HTTP status codes.
    """
    description, value = InputValidator.validate_calculation_input(data)

    match = tariff_service.find_match(description)

    fees = fee_calculator.calculate_fees(
        value=value,
        tariff_rate=match.tariff_rate,
        matched_description=match.description,
        confidence=match.confidence,
    )

    if calculation_logger is not None:
        calculation_logger.log(description, value, request_id)

    return {
        "matched_description": fees.matched_description,
        "matched_group": match.group,
        "hts_number": match.hts_number,
        "match_source": match.match_source,
        "confidence": round(fees.confidence, 3),
        "tariff": fees.tariff_rate,
        "duty": fees.duty,
        "merchandise_processing_fee": fees.merchandise_processing_fee,
        "harbor_maintenance_fee": fees.harbor_maintenance_fee,
        "subtotal": fees.subtotal,
        "footnote": FOOTNOTE,
        "request_id": request_id,
    }
