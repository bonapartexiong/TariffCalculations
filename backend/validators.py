"""
Input validation with whitelist-based sanitization.
"""

import re
from decimal import Decimal
from typing import Tuple

from backend.config import (
    MIN_DESCRIPTION_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MIN_VALUE,
    MAX_VALUE,
)
from backend.exceptions import ValidationError

# Allowed pattern for product descriptions:
# letters (including accented), digits, spaces, hyphens, commas, periods,
# forward slashes, ampersands, parentheses, and percent signs.
_DESCRIPTION_RE = re.compile(r"^[\w\s\-.,/&()%]+$", re.UNICODE)


class InputValidator:
    """Validates API inputs against allow-lists and value bounds."""

    @staticmethod
    def validate_calculation_input(data: dict) -> Tuple[str, float]:
        """Validate input for tariff calculation.

        Returns (description, value) on success.
        """
        if not data:
            raise ValidationError("Request body is empty")

        # --- Description ---
        if "description" not in data:
            raise ValidationError("Missing required field: description")

        description = str(data["description"]).strip()

        if len(description) < MIN_DESCRIPTION_LENGTH:
            raise ValidationError(
                f"Description too short (minimum {MIN_DESCRIPTION_LENGTH} characters)"
            )

        if len(description) > MAX_DESCRIPTION_LENGTH:
            raise ValidationError(
                f"Description too long (maximum {MAX_DESCRIPTION_LENGTH} characters)"
            )

        if not _DESCRIPTION_RE.match(description):
            raise ValidationError(
                "Description contains invalid characters. "
                "Allowed: letters, digits, spaces, hyphens, commas, "
                "periods, slashes, ampersands, parentheses, percent."
            )

        # --- Value ---
        if "value" not in data:
            raise ValidationError("Missing required field: value")

        try:
            value = float(data["value"])
        except (TypeError, ValueError):
            raise ValidationError("Value must be a valid number")

        value_d = Decimal(str(value))

        if value_d < MIN_VALUE:
            raise ValidationError(f"Value must be at least ${MIN_VALUE}")

        if value_d > MAX_VALUE:
            raise ValidationError(f"Value cannot exceed ${MAX_VALUE:,.2f}")

        return description, value
