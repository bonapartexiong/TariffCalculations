"""
Customs duty and fee calculator using Decimal arithmetic.
"""

from decimal import Decimal, ROUND_HALF_UP

from backend.config import MPF_RATE, MPF_CAP, HMF_RATE, logger
from backend.exceptions import TariffCalculationError
from backend.models import FeeBreakdown


def _to_cents(amount: Decimal) -> Decimal:
    """Round a Decimal to two decimal places using banker's rounding."""
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class FeeCalculator:
    """Calculates customs duties, MPF, HMF, and subtotal."""

    @staticmethod
    def calculate_fees(
        value: float,
        tariff_rate: float,
        matched_description: str,
        confidence: float,
    ) -> FeeBreakdown:
        """Calculate all fees and duties for a given product value."""
        try:
            value_d = Decimal(str(value))
            rate_d = Decimal(str(tariff_rate))

            duty = value_d * rate_d
            mpf = min(value_d * MPF_RATE, MPF_CAP)
            hmf = value_d * HMF_RATE
            subtotal = duty + mpf + hmf

            return FeeBreakdown(
                duty=float(_to_cents(duty)),
                merchandise_processing_fee=float(_to_cents(mpf)),
                harbor_maintenance_fee=float(_to_cents(hmf)),
                subtotal=float(_to_cents(subtotal)),
                tariff_rate=tariff_rate,
                matched_description=matched_description,
                confidence=confidence,
            )

        except Exception as exc:
            logger.error("Error calculating fees: %s", exc)
            raise TariffCalculationError(f"Fee calculation failed: {exc}")
