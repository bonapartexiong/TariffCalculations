"""GET /v1/health: status, revision, chunks, source_pdf_sha256,
build_date, engine, index_path / index_exists."""

from __future__ import annotations

try:
    from flask import Blueprint, jsonify
except Exception:  # pragma: no cover - Flask not installed.
    Blueprint = None  # type: ignore

from backend.services import pipeline

health_bp = Blueprint("health", __name__) if Blueprint else None

if health_bp is not None:

    @health_bp.route("/v1/health", methods=["GET"])
    def health():
        return jsonify(pipeline.health_info()), 200
