"""Configuration for Module C.

Every value comes from the environment, parsed with safe helpers that
never crash on bad input: a malformed env var falls back to the default
instead of taking the process down at import time.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_INDEX_PATH = str(PROJECT_ROOT / "data" / "index.jsonl")
DEFAULT_PDF_PATH = str(
    Path.home() / "workspace" / "user" / "files" / "2026HTSRev21_0_cgh9.pdf"
)

# CBP fee defaults (EXTERNAL - not in the HTS PDF). See CONTRACTS.md.
# Overridable via env for tests / fee updates; never sourced from the PDF.
DEFAULT_MPF_RATE = 0.003464  # 0.3464%
DEFAULT_MPF_MIN = 32.71
DEFAULT_MPF_CAP = 575.0
DEFAULT_HMF_RATE = 0.00125  # 0.125%, ocean-only


def safe_int(raw, default):
    """Parse an int from any env value; return ``default`` on any failure."""
    try:
        if raw is None:
            return default
        if isinstance(raw, bool):
            return default
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if isinstance(value, bool):
        return default
    return value


def safe_float(raw, default):
    """Parse a finite float from any env value; ``default`` on any failure."""
    try:
        if raw is None:
            return default
        if isinstance(raw, bool):
            return default
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return value


def safe_bool(raw, default=False):
    """Parse a bool from common env spellings; ``default`` otherwise."""
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return default
    text = str(raw).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return default


def parse_origins(raw):
    """Split a comma-separated origin list, stripping whitespace.

    Empty entries are dropped, duplicates are removed (order kept).
    Never crashes on bad input.
    """
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple)):
        items = [str(item) for item in raw]
    else:
        items = str(raw).split(",")
    seen = set()
    origins = []
    for item in items:
        origin = item.strip()
        if origin and origin not in seen:
            seen.add(origin)
            origins.append(origin)
    return tuple(origins)


@dataclass(frozen=True)
class Settings:
    index_path: str = DEFAULT_INDEX_PATH
    pdf_path: str = DEFAULT_PDF_PATH
    port: int = 5000
    allowed_origins: tuple = ()
    # Rate limit: requests per minute per client IP (0 disables).
    rate_limit_per_minute: int = 60
    # CBP fee overrides (external config, not PDF data).
    mpf_rate: float = DEFAULT_MPF_RATE
    mpf_min: float = DEFAULT_MPF_MIN
    mpf_cap: float = DEFAULT_MPF_CAP
    hmf_rate: float = DEFAULT_HMF_RATE
    debug: bool = False


def get_settings(environ=None):
    """Build Settings from ``environ`` (defaults to ``os.environ``)."""
    env = os.environ if environ is None else environ

    def _get(name, default=None):
        try:
            value = env.get(name, default)
        except Exception:
            return default
        return default if value is None else value

    port = safe_int(_get("PORT", 5000), 5000)
    if port < 1 or port > 65535:
        port = 5000
    rate_limit = safe_int(_get("RATE_LIMIT_PER_MINUTE", 60), 60)
    if rate_limit < 0:
        rate_limit = 0

    return Settings(
        index_path=str(_get("INDEX_PATH", DEFAULT_INDEX_PATH) or DEFAULT_INDEX_PATH),
        pdf_path=str(_get("PDF_PATH", DEFAULT_PDF_PATH) or DEFAULT_PDF_PATH),
        port=port,
        allowed_origins=parse_origins(_get("ALLOWED_ORIGINS", "")),
        rate_limit_per_minute=rate_limit,
        mpf_rate=safe_float(_get("MPF_RATE", DEFAULT_MPF_RATE), DEFAULT_MPF_RATE),
        mpf_min=safe_float(_get("MPF_MIN", DEFAULT_MPF_MIN), DEFAULT_MPF_MIN),
        mpf_cap=safe_float(_get("MPF_CAP", DEFAULT_MPF_CAP), DEFAULT_MPF_CAP),
        hmf_rate=safe_float(_get("HMF_RATE", DEFAULT_HMF_RATE), DEFAULT_HMF_RATE),
        debug=safe_bool(_get("FLASK_DEBUG", False), False),
    )


def fee_config(settings=None):
    """Return the MPF/HMF config dict the calculator accepts as overrides."""
    settings = settings or get_settings()
    return {
        "mpf_rate": settings.mpf_rate,
        "mpf_min": settings.mpf_min,
        "mpf_cap": settings.mpf_cap,
        "hmf_rate": settings.hmf_rate,
    }
