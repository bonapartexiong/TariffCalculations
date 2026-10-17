"""Ingestion package: bbox-based HTS PDF parser.

Imports are lazy so ``python -m rag.ingest.parse_hts_pdf`` does not
trigger a duplicate-module RuntimeWarning.
"""

__all__ = ["HTSParser", "parse_pdf", "validate_chunks"]


def __getattr__(name):
    if name in __all__:
        from .parse_hts_pdf import HTSParser, parse_pdf, validate_chunks

        return {"HTSParser": HTSParser, "parse_pdf": parse_pdf, "validate_chunks": validate_chunks}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
