"""POST /v1/calculate.

Accepts the backward-compatible body ``{description, value}`` plus the
extended CONTRACTS fields (quantity, quantity_unit, origin_country,
program, mode, quota_status, chapter99_stack, de_minimis, hts_code,
mpf_config, hmf_config). All work is delegated to
``backend.services.pipeline.run_calculate``.
"""

from __future__ import annotations

try:
    from flask import Blueprint, jsonify, request
except Exception:  # pragma: no cover - Flask not installed.
    Blueprint = None  # type: ignore

from backend.exceptions import ApiError, ValidationError
from backend.services import pipeline

calculate_bp = Blueprint("calculate", __name__) if Blueprint else None

if calculate_bp is not None:

    @calculate_bp.route("/v1/calculate", methods=["POST"])
    def calculate():
        payload = request.get_json(silent=True)
        if payload is None:
            raise ValidationError("Request body must be a valid JSON object.")
        if not isinstance(payload, dict):
            raise ValidationError("Request body must be a JSON object.")
        result = pipeline.run_calculate(payload)
        return jsonify(result), 200
