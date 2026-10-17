"""Build the tariff index (JSONL + meta.json) from the HTS PDF only.

Usage:
    python scripts/build_index.py --pdf ~/workspace/user/files/2026HTSRev21_0_cgh9.pdf --out data/index.jsonl
    python scripts/build_index.py --sample            # first pages only, smoke test
    python scripts/build_index.py --pages 1-10 --validate
    python scripts/build_index.py --pdf ... --out data/index.jsonl --validate

Ground truth is the HTS 2026 Rev 21 PDF (Pub. 5802). This script never
reads, writes, or otherwise touches any spreadsheet source.

The actual PDF parsing lives in ``rag.ingest.parse_hts_pdf`` (another
builder owns it); this script is the CLI around it: it resolves args,
streams a SHA-256 of the PDF, writes one JSONL line per chunk plus a
``meta.json`` sidecar, and optionally validates the output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

REVISION = "2026 Rev 21"
PUBLICATION = "Pub. 5802"
SOURCE_PDF = "2026HTSRev21_0_cgh9.pdf"
SAMPLE_PAGES = 5  # fallback only; --sample prefers parser SAMPLE_PAGES

REQUIRED_CHUNK_FIELDS = ("id", "kind", "text", "revision")


def parse_pages(spec):
    """Parse --pages: '1-10', '1,3,5', or a single page -> sorted list."""
    pages = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            start, end = int(start), int(end)
            if start < 1 or end < start:
                raise ValueError(f"Invalid page range: {part!r}")
            pages.update(range(start, end + 1))
        else:
            page = int(part)
            if page < 1:
                raise ValueError(f"Invalid page: {part!r}")
            pages.add(page)
    return sorted(pages)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def call_parser(pdf_path, sample, pages):
    """Resolve and call the ingest parser across its landed API shapes.

    The ingest builder landed ``rag.ingest.parse_hts_pdf`` as a module
    exposing ``parse_pdf(pdf_path, pages=[...]) -> {"chunks", ...}``,
    while CONTRACTS names the entry ``parse_hts_pdf``. Accept either:
    a callable ``parse_hts_pdf`` attribute, or the module's ``parse_pdf``.
    """
    try:
        from rag.ingest import parse_hts_pdf as target
    except Exception as exc:
        raise RuntimeError(
            "rag.ingest.parse_hts_pdf is unavailable; the ingest builder "
            f"has not landed it yet ({exc})."
        ) from exc
    func = None
    if callable(target):
        func = target
    else:
        for attr in ("parse_pdf", "parse"):
            candidate = getattr(target, attr, None)
            if callable(candidate):
                func = candidate
                break
    if func is None:
        raise RuntimeError(
            "rag.ingest.parse_hts_pdf exposes no callable parser "
            "(expected parse_hts_pdf() or parse_pdf())."
        )
    if getattr(func, "__name__", "") in ("parse_pdf", "parse"):
        # parse_pdf(pdf_path, pages=[...]); sample already mapped to pages.
        return func(str(pdf_path), pages=pages)
    attempts = [
        {"sample": sample, "pages": pages},
        {"sample": sample},
        {"pages": pages},
        {},
    ]
    last_error = None
    for kwargs in attempts:
        try:
            return func(str(pdf_path), **kwargs)
        except TypeError as exc:
            last_error = exc
            continue
    raise RuntimeError(f"Could not call parse_hts_pdf with any known signature: {last_error}")


def normalise_chunks(result):
    """Accept a list, generator, (chunks, meta) tuple, or {'chunks': [...]}."""
    meta = {}
    if isinstance(result, tuple) and len(result) == 2:
        chunks, meta = result
    elif isinstance(result, dict) and "chunks" in result:
        chunks, meta = result["chunks"], result
    else:
        chunks = result
    chunk_list = list(chunks or [])
    return chunk_list, (meta if isinstance(meta, dict) else {})


def validate_chunks(chunks):
    """Check CONTRACTS chunk shape. Returns a list of error strings."""
    errors = []
    if not chunks:
        errors.append("No chunks produced.")
        return errors
    for i, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            errors.append(f"Chunk {i} is not an object.")
            continue
        for field in REQUIRED_CHUNK_FIELDS:
            if field not in chunk:
                errors.append(f"Chunk {i} ({chunk.get('id')}) missing field {field!r}.")
        if chunk.get("revision") != REVISION:
            errors.append(
                f"Chunk {i} ({chunk.get('id')}) revision "
                f"{chunk.get('revision')!r} != {REVISION!r}."
            )
        if not isinstance(chunk.get("text"), str) or not chunk.get("text", "").strip():
            errors.append(f"Chunk {i} ({chunk.get('id')}) has empty text.")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the HTS index from the PDF.")
    parser.add_argument("--pdf", default=None,
                        help="Path to the HTS PDF (default: $PDF_PATH or the known Rev 21 file).")
    parser.add_argument("--out", default=str(PROJECT_ROOT / "data" / "index.jsonl"),
                        help="Output JSONL path (default: data/index.jsonl).")
    parser.add_argument("--sample", action="store_true",
                        help=f"Build a sample index from the first {SAMPLE_PAGES} pages only.")
    parser.add_argument("--pages", default=None,
                        help="Page subset, e.g. '1-10' or '1,3,5'. Overrides --sample.")
    parser.add_argument("--validate", action="store_true",
                        help="Validate chunk shape / revision after parsing.")
    args = parser.parse_args(argv)

    import os

    pdf_path = Path(
        args.pdf or os.environ.get("PDF_PATH")
        or (Path.home() / "workspace" / "user" / "files" / SOURCE_PDF)
    ).expanduser()
    if pdf_path.suffix.lower() != ".pdf":
        print(f"ERROR: --pdf must point at a PDF file, got {pdf_path}", file=sys.stderr)
        return 2
    if not pdf_path.exists():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        return 2
    if pdf_path.name != SOURCE_PDF:
        print(f"WARNING: expected {SOURCE_PDF}, got {pdf_path.name}; continuing.", file=sys.stderr)

    pages = None
    if args.pages:
        try:
            pages = parse_pages(args.pages)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    sample = args.sample
    if sample and pages is None:
        # Use the parser's curated sample set (GRI + GN3 + Ch61 + Ch85 +
        # Ch99 + appendix), NOT the first N pages (which are just cover/TOC).
        try:
            from rag.ingest.parse_hts_pdf import SAMPLE_PAGES as PARSER_SAMPLE_PAGES
            pages = list(PARSER_SAMPLE_PAGES)
        except Exception:
            pages = list(range(1, SAMPLE_PAGES + 1))

    try:
        result = call_parser(pdf_path, sample=sample, pages=pages)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    chunks, parser_meta = normalise_chunks(result)

    if args.validate:
        errors = validate_chunks(chunks)
        if errors:
            print("VALIDATION FAILED:", file=sys.stderr)
            for error in errors[:20]:
                print(f"  - {error}", file=sys.stderr)
            return 3
        print(f"Validation passed: {len(chunks)} chunks.")

    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    total_pages = parser_meta.get("pages") or parser_meta.get("page_count")
    if total_pages is None:
        try:
            import fitz

            with fitz.open(str(pdf_path)) as doc:
                total_pages = len(doc)
        except Exception:
            total_pages = None
    meta = {
        "revision": REVISION,
        "publication": PUBLICATION,
        "source_pdf": SOURCE_PDF,
        "source_pdf_sha256": sha256_file(pdf_path),
        "build_date": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "chunk_count": len(chunks),
        "pages": total_pages,
        "sample": bool(sample),
        "pages_subset": pages,
    }
    # Per-output meta (index.meta.json), NOT a shared meta.json:
    # a later --sample build must not silently restamp the full index.
    meta_path = out_path.with_suffix(".meta.json")
    with open(meta_path, "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    print(f"Wrote {len(chunks)} chunks -> {out_path}")
    print(f"Wrote meta -> {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
