"""Route blueprints for Module C."""

from __future__ import annotations

__all__ = ["calculate_bp", "health_bp", "rag_bp"]


def __getattr__(name):
    if name == "calculate_bp":
        from backend.routes.calculate import calculate_bp

        return calculate_bp
    if name == "rag_bp":
        from backend.routes.rag import rag_bp

        return rag_bp
    if name == "health_bp":
        from backend.routes.health import health_bp

        return health_bp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
