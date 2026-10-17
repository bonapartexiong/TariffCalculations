"""Backend package for the TariffCalculations-RAG API (Module C).

Importing this package must never create a Flask app and must never
require Flask to be installed. Use ``backend.app.create_app()`` to build
one. All heavy lifting lives in ``backend.services.pipeline`` so the
Flask routes and the Netlify adapters share one implementation.
"""

from __future__ import annotations

__all__ = ["create_app"]


def __getattr__(name):
    if name == "create_app":
        from backend.app import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
