"""POST /v1/rag/query -> ``rag/answer.py`` via the shared pipeline."""

from __future__ import annotations

try:
    from flask import Blueprint, jsonify, request
except Exception:  # pragma: no cover - Flask not installed.
    Blueprint = None  # type: ignore

from backend.exceptions import ValidationError
from backend.services import pipeline

rag_bp = Blueprint("rag", __name__) if Blueprint else None

if rag_bp is not None:

    @rag_bp.route("/v1/rag/query", methods=["POST"])
    def rag_query():
        payload = request.get_json(silent=True)
        if payload is None:
            raise ValidationError("Request body must be a valid JSON object.")
        if not isinstance(payload, dict):
            raise ValidationError("Request body must be a JSON object.")
        result = pipeline.run_rag_query(payload)
        return jsonify(result), 200

    @rag_bp.route("/v1/candidates", methods=["POST"])
    def candidates():
        payload = request.get_json(silent=True)
        if payload is None:
            raise ValidationError("Request body must be a valid JSON object.")
        if not isinstance(payload, dict):
            raise ValidationError("Request body must be a JSON object.")
        result = pipeline.run_candidates(payload)
        return jsonify(result), 200
