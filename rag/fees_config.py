"""MPF / HMF fee configuration.

The Merchandise Processing Fee (MPF) and Harbor Maintenance Fee (HMF)
rates are NOT in the HTS PDF. They are CBP FY2025 values published
externally, so every value produced from this module is labeled as such
(``CBP FY2025 EXTERNAL (not in HTS PDF)``).

All amounts are :class:`decimal.Decimal` -- never float.

Defaults may be overridden per call (e.g. by the calculator) or via
environment variables:

- ``MPF_RATE`` -- e.g. ``0.003464``
- ``MPF_MIN``  -- e.g. ``32.71``
- ``MPF_CAP``  -- e.g. ``575``
- ``HMF_RATE`` -- e.g. ``0.00125``
"""

from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

SOURCE = "CBP FY2025 EXTERNAL (not in HTS PDF)"
"""Provenance label for every MPF/HMF value (external to the HTS PDF)."""

# Backwards-compatible alias for the provenance label.
FEES_SOURCE = SOURCE

# CBP FY2025 defaults (EXTERNAL -- not in HTS PDF).
MPF_RATE = Decimal("0.003464")
"""MPF ad valorem rate: 0.3464%."""

MPF_MIN = Decimal("32.71")
"""MPF minimum fee in dollars."""

MPF_CAP = Decimal("575")
"""MPF maximum fee (cap) in dollars."""

MPF_SOURCE = SOURCE

HMF_RATE = Decimal("0.00125")
"""HMF ad valorem rate: 0.125% (ocean shipments only)."""

HMF_SOURCE = SOURCE


def _decimal_env(name: str, default: Decimal) -> Decimal:
    """Read a Decimal from the environment, falling back to ``default``."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return Decimal(raw.strip())
    except InvalidOperation:
        raise ValueError(f"Environment variable {name}={raw!r} is not a valid Decimal")


def _as_decimal(value: Any, name: str) -> Decimal:
    """Coerce an override value to Decimal without going through float."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, str)):
        try:
            return Decimal(str(value).strip())
        except InvalidOperation:
            raise ValueError(f"Override {name}={value!r} is not a valid Decimal")
    raise ValueError(f"Override {name}={value!r} must be a Decimal, int, or str")


def get_mpf_config(overrides: Mapping[str, Any] | None = None) -> dict:
    """Return the MPF configuration.

    Keys: ``rate`` (Decimal), ``min`` (Decimal), ``cap`` (Decimal),
    ``source`` (str). ``overrides`` may supply any of ``rate``/``min``/``cap``.
    """
    cfg = {
        "rate": _decimal_env("MPF_RATE", MPF_RATE),
        "min": _decimal_env("MPF_MIN", MPF_MIN),
        "cap": _decimal_env("MPF_CAP", MPF_CAP),
        "source": MPF_SOURCE,
    }
    if overrides:
        for key in ("rate", "min", "cap"):
            if key in overrides and overrides[key] is not None:
                cfg[key] = _as_decimal(overrides[key], f"mpf {key}")
        if "source" in overrides and overrides["source"] is not None:
            cfg["source"] = str(overrides["source"])
    return cfg


def get_hmf_config(overrides: Mapping[str, Any] | None = None) -> dict:
    """Return the HMF configuration.

    Keys: ``rate`` (Decimal), ``ocean_only`` (bool), ``source`` (str).
    ``overrides`` may supply ``rate``.
    """
    cfg = {
        "rate": _decimal_env("HMF_RATE", HMF_RATE),
        "ocean_only": True,
        "source": HMF_SOURCE,
    }
    if overrides:
        if "rate" in overrides and overrides["rate"] is not None:
            cfg["rate"] = _as_decimal(overrides["rate"], "hmf rate")
        if "ocean_only" in overrides and overrides["ocean_only"] is not None:
            cfg["ocean_only"] = bool(overrides["ocean_only"])
        if "source" in overrides and overrides["source"] is not None:
            cfg["source"] = str(overrides["source"])
    return cfg


def get_fee_config(
    mpf_overrides: Mapping[str, Any] | None = None,
    hmf_overrides: Mapping[str, Any] | None = None,
) -> dict:
    """Return ``{"mpf": ..., "hmf": ...}`` with both fee configurations."""
    return {"mpf": get_mpf_config(mpf_overrides), "hmf": get_hmf_config(hmf_overrides)}
