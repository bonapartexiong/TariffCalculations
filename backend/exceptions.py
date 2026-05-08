"""
Custom exception hierarchy for the Tariff Calculation API.
"""


class TariffCalculationError(Exception):
    """Base exception for tariff calculations."""
    pass


class ProductMatchError(TariffCalculationError):
    """Could not match product description."""
    pass


class ValidationError(TariffCalculationError):
    """Input validation failed."""
    pass


class DatabaseError(TariffCalculationError):
    """Database operation failed."""
    pass
