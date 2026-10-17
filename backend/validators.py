"""Request validation for Module C.

Rules (see CONTRACTS.md + task spec):

* ``description``: required string, 3-500 chars. Apostrophes,
  semicolons, commas, quotes, ``+`` and ``%`` are explicitly ALLOWED
  (they are ordinary description punctuation). Only control characters
  are rejected.
* ``value``: required, 0.01-100M as :class:`decimal.Decimal`. Booleans,
  NaN and infinities are rejected outright (``bool`` is a subclass of
  ``int`` in Python, so it must be checked first).
* ``quantity``: optional, non-negative Decimal.
* ``mode``: ocean | air | land | mail. ``quota_status``: in | out |
  unknown. ``chapter99_stack``: list of HTS strings.
* ``validate_rag_query``: question 3-2000 chars, top_k clamped 1-20.
* ``clamp_confidence``: any input -> float in [0, 1]; NaN/None/bool
  degrade to 0.0 instead of raising.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation

from backend.exceptions import ValidationError

MIN_VALUE = Decimal("0.01")
MAX_VALUE = Decimal("100000000")  # 100M
MAX_DESCRIPTION_LEN = 500
MIN_DESCRIPTION_LEN = 3
MAX_QUESTION_LEN = 2000
MIN_QUESTION_LEN = 3
MAX_QUANTITY = Decimal("1000000000000")  # 1e12

_MODES = ("ocean", "air", "land", "mail")
_QUOTA_STATUSES = ("in", "out", "unknown")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _fail(message):
    raise ValidationError(message)


def _to_decimal(value, field):
    """Coerce ``value`` to Decimal, rejecting bool / NaN / infinity."""
    if isinstance(value, bool) or value is None:
        _fail(f"Field '{field}' must be a finite number, not a boolean or null.")
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail(f"Field '{field}' must be a finite number.")
    if isinstance(value, (int, float, Decimal)):
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError):
            _fail(f"Field '{field}' must be a finite number.")
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            _fail(f"Field '{field}' must be a finite number.")
        try:
            result = Decimal(text)
        except InvalidOperation:
            _fail(f"Field '{field}' must be a finite number.")
    else:
        _fail(f"Field '{field}' must be a finite number.")
    if not result.is_finite():
        _fail(f"Field '{field}' must be a finite number.")
    return result


def _clean_text(value, field, min_len, max_len, required=True):
    if value is None:
        if required:
            _fail(f"Field '{field}' is required.")
        return None
    if not isinstance(value, str):
        _fail(f"Field '{field}' must be a string.")
    text = value.strip()
    if _CONTROL_RE.search(text):
        _fail(f"Field '{field}' contains control characters.")
    if len(text) < min_len:
        _fail(f"Field '{field}' must be at least {min_len} characters.")
    if len(text) > max_len:
        _fail(f"Field '{field}' must be at most {max_len} characters.")
    return text


def _dotted_hts(digits):
    """Canonical dotted form: 99038801 -> 9903.88.01, 420221 -> 4202.21,
    4202110010 -> 4202.11.00.10. Dots are part of the HTS identity and
    must survive validation (previously stripped, producing '99038801')."""
    if len(digits) <= 4:
        return digits
    parts = [digits[:4]]
    rest = digits[4:]
    while rest:
        parts.append(rest[:2])
        rest = rest[2:]
    return ".".join(parts)


def _clean_hts(value, field):
    """Normalise an HTS code to canonical dotted form."""
    if not isinstance(value, str):
        _fail(f"Field '{field}' must be a string HTS code.")
    code = value.strip().replace(" ", "").replace("-", "").replace(".", "")
    if not code or len(code) > 10 or not code.isdigit():
        _fail(f"Field '{field}' must be a numeric HTS code.")
    return _dotted_hts(code)


def _clean_fee_override(value, field):
    """Validate fee overrides; accept calculator-style aliases.

    Canonical keys are mpf_rate/mpf_min/mpf_cap/hmf_rate. Aliases
    rate/min/cap (the calculator's own key names) are accepted and
    normalised to canonical keys so both sides agree.
    """
    if not isinstance(value, dict):
        _fail(f"Field '{field}' must be an object.")
    aliases = {
        "mpf_config": {"mpf_rate": "mpf_rate", "rate": "mpf_rate",
                       "mpf_min": "mpf_min", "min": "mpf_min",
                       "mpf_cap": "mpf_cap", "cap": "mpf_cap"},
        "hmf_config": {"hmf_rate": "hmf_rate", "rate": "hmf_rate"},
    }.get(field, {})
    cleaned = {}
    for key, raw in value.items():
        canonical = aliases.get(key)
        if canonical is None:
            _fail(f"Field '{field}.{key}' is not a supported fee override.")
        number = _to_decimal(raw, f"{field}.{key}")
        if number < 0:
            _fail(f"Field '{field}.{key}' must be >= 0.")
        cleaned[canonical] = number
    return cleaned


def validate_calculate(payload):
    """Validate a POST /v1/calculate body; return a cleaned dict."""
    if not isinstance(payload, dict):
        _fail("Request body must be a JSON object.")
    cleaned = {}

    cleaned["description"] = _clean_text(
        payload.get("description"), "description",
        MIN_DESCRIPTION_LEN, MAX_DESCRIPTION_LEN,
    )
    if "value" not in payload or payload.get("value") is None:
        _fail("Field 'value' is required.")
    value = _to_decimal(payload.get("value"), "value")
    if value < MIN_VALUE or value > MAX_VALUE:
        _fail("Field 'value' must be between 0.01 and 100000000.")
    cleaned["value"] = value

    if payload.get("hts_code") is not None:
        cleaned["hts_code"] = _clean_hts(payload.get("hts_code"), "hts_code")

    if payload.get("quantity") is not None:
        quantity = _to_decimal(payload.get("quantity"), "quantity")
        if quantity < 0 or quantity > MAX_QUANTITY:
            _fail("Field 'quantity' must be between 0 and 1000000000000.")
        cleaned["quantity"] = quantity
    if payload.get("quantity_unit") is not None:
        cleaned["quantity_unit"] = _clean_text(
            payload.get("quantity_unit"), "quantity_unit", 1, 50
        )

    if payload.get("origin_country") is not None:
        country = _clean_text(
            payload.get("origin_country"), "origin_country", 2, 56
        )
        cleaned["origin_country"] = country

    if payload.get("program") is not None:
        cleaned["program"] = _clean_text(payload.get("program"), "program", 1, 50)

    if payload.get("mode") is not None:
        mode = payload.get("mode")
        if not isinstance(mode, str) or mode.strip().lower() not in _MODES:
            _fail("Field 'mode' must be one of: ocean, air, land, mail.")
        cleaned["mode"] = mode.strip().lower()

    if payload.get("quota_status") is not None:
        quota = payload.get("quota_status")
        if not isinstance(quota, str) or quota.strip().lower() not in _QUOTA_STATUSES:
            _fail("Field 'quota_status' must be one of: in, out, unknown.")
        cleaned["quota_status"] = quota.strip().lower()

    if payload.get("chapter99_stack") is not None:
        stack = payload.get("chapter99_stack")
        if not isinstance(stack, list):
            _fail("Field 'chapter99_stack' must be a list of HTS codes.")
        if len(stack) > 20:
            _fail("Field 'chapter99_stack' may contain at most 20 codes.")
        cleaned["chapter99_stack"] = [
            _clean_hts(code, "chapter99_stack") for code in stack
        ]

    if payload.get("de_minimis") is not None:
        if not isinstance(payload.get("de_minimis"), bool):
            _fail("Field 'de_minimis' must be a boolean.")
        cleaned["de_minimis"] = payload.get("de_minimis")

    if payload.get("mpf_config") is not None:
        cleaned["mpf_config"] = _clean_fee_override(payload.get("mpf_config"), "mpf_config")
    if payload.get("hmf_config") is not None:
        cleaned["hmf_config"] = _clean_fee_override(payload.get("hmf_config"), "hmf_config")

    return cleaned


def validate_rag_query(payload):
    """Validate a POST /v1/rag/query body; return a cleaned dict."""
    if not isinstance(payload, dict):
        _fail("Request body must be a JSON object.")
    cleaned = {
        "question": _clean_text(
            payload.get("question"), "question",
            MIN_QUESTION_LEN, MAX_QUESTION_LEN,
        )
    }
    top_k = payload.get("top_k", 5)
    if isinstance(top_k, bool) or top_k is None:
        _fail("Field 'top_k' must be an integer.")
    try:
        top_k = int(top_k) if not isinstance(top_k, float) else int(top_k)
    except (TypeError, ValueError):
        _fail("Field 'top_k' must be an integer.")
    cleaned["top_k"] = max(1, min(20, top_k))
    return cleaned


def clamp_confidence(value):
    """Clamp any input to a float in [0, 1]; never raises."""
    try:
        if isinstance(value, bool) or value is None:
            return 0.0
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number):
        return 0.0
    if math.isinf(number):
        return 1.0 if number > 0 else 0.0
    return max(0.0, min(1.0, number))
