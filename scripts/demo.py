"""Demo: run 5 grounded queries against the index and print results.

Index resolution (in order):
  1. ``data/index.jsonl`` if it exists -> REAL index.
  2. Else a SAMPLE index built via the parser (``--sample`` semantics:
     first pages only). Results may abstain if the sample lacks the
     queried lines; that is reported honestly, not papered over.
  3. Else synthetic fallback chunks -> every output block is labelled
     ``DEMO SYNTHETIC`` and carries no real rate. Synthetic output is
     NEVER presented as real HTS data.

Run:  python scripts/demo.py [--index PATH] [--synthetic]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.services import pipeline  # noqa: E402

REVISION = "2026 Rev 21"
PUBLICATION = "Pub. 5802"
SYNTHETIC_BANNER = "DEMO SYNTHETIC — NOT FROM THE HTS PDF — NO REAL RATE"

QUERIES = [
    {
        "label": "(a) Leather handbag — 4202.21.90 (valued over $20 each), value $500",
        "kind": "calculate",
        "payload": {"description": "Leather handbag", "hts_code": "4202.21.90", "value": 500},
    },
    {
        "label": "(b) Electric motor — 8501.51.20, value $1000",
        "kind": "calculate",
        "payload": {"description": "Electric motor", "hts_code": "8501.51.20", "value": 1000},
    },
    {
        "label": "(c1) Peanuts TRQ — in-quota 1202.41.05, 100 kg, value $1000",
        "kind": "calculate",
        "payload": {"description": "Peanuts, in-quota", "hts_code": "1202.41.05",
                    "value": 1000, "quota_status": "in",
                    "quantity": 100, "quantity_unit": "kg"},
    },
    {
        "label": "(c2) Peanuts TRQ — out-of-quota 1202.30.80, value $1000",
        "kind": "calculate",
        "payload": {"description": "Peanuts, out-of-quota", "hts_code": "1202.30.80",
                    "value": 1000, "quota_status": "out"},
    },
    {
        "label": "(c3) Peanuts TRQ — explain in-quota vs out-of-quota",
        "kind": "rag",
        "payload": {"question": "Peanuts TRQ: explain in-quota 1202.41.05 vs "
                                 "out-of-quota 1202.30.80 rates and how the quota works."},
    },
    {
        "label": "(d) China Section 301 stack — 8501.51.20 + 9903.88.01, value $1000",
        "kind": "calculate",
        "payload": {"description": "Electric motor from China, Section 301 stack",
                    "hts_code": "8501.51.20", "value": 1000,
                    "origin_country": "China", "chapter99_stack": ["9903.88.01"]},
    },
    {
        "label": "(e) GSP — Is Free (A) claimable?",
        "kind": "rag",
        "payload": {"question": "Is Free (A) claimable under GSP right now? "
                                 "Explain the current status of the GSP program."},
    },
]


def synthetic_chunks():
    """Placeholder chunks with NO rates. Labels make them unmistakable."""
    base = {
        "kind": "tariff_line", "text": "", "general_raw": None, "special_raw": None,
        "col2_raw": None, "special_programs": [], "footnotes": [], "chapter": None,
        "section": None, "revision": REVISION, "publication": PUBLICATION,
        "effective_period": None, "stacking": "unknown",
        "reference_only": True, "dutiable": False,
    }
    chunks = []
    for hts, desc, page, kind in [
        ("4202.21", "Leather handbag (synthetic placeholder)", None, "tariff_line"),
        ("8501.51.20", "Electric motor (synthetic placeholder)", None, "tariff_line"),
        ("1202.41.05", "Peanuts in-quota (synthetic placeholder)", None, "tariff_line"),
        ("1202.30.80", "Peanuts out-of-quota (synthetic placeholder)", None, "tariff_line"),
        ("9903.88.01", "Section 301 additional duty (synthetic placeholder)", None, "chapter99"),
    ]:
        chunk = dict(base)
        chunk.update({
            "id": f"hts:{hts}",
            "kind": kind,
            "hts": hts,
            "description": desc,
            "unit": None,
            "page": page,
            "synthetic": True,
            "text": f"{SYNTHETIC_BANNER}: {desc}. No rate is stated; "
                    "this placeholder exists only so the demo can show "
                    "its abstain/uncomputable path when the PDF is unavailable.",
        })
        chunks.append(chunk)
    return chunks


def build_sample_index(tmpdir):
    """Build a sample index via the parser; return path or None."""
    try:
        from rag.ingest import parse_hts_pdf  # noqa: F401
    except Exception:
        print("[demo] Parser unavailable; using synthetic fallback.", file=sys.stderr)
        return None
    import os

    pdf = Path(os.environ.get(
        "PDF_PATH",
        Path.home() / "workspace" / "user" / "files" / "2026HTSRev21_0_cgh9.pdf",
    )).expanduser()
    if not pdf.exists():
        print(f"[demo] PDF not found at {pdf}; using synthetic fallback.", file=sys.stderr)
        return None
    try:
        from scripts.build_index import main as build_main  # type: ignore
    except Exception:
        sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
        from build_index import main as build_main  # type: ignore
    out = Path(tmpdir) / "sample-index.jsonl"
    code = build_main(["--pdf", str(pdf), "--out", str(out), "--sample"])
    if code != 0 or not out.exists():
        print("[demo] Sample index build failed; using synthetic fallback.", file=sys.stderr)
        return None
    return str(out)


def write_chunks(path, chunks):
    with open(path, "w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    meta = {
        "revision": REVISION, "publication": PUBLICATION,
        "source_pdf": "2026HTSRev21_0_cgh9.pdf", "source_pdf_sha256": None,
        "build_date": None, "chunk_count": len(chunks), "pages": None,
        "sample": True, "synthetic": True,
    }
    with open(Path(path).parent / "meta.json", "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2)


def _shorten(result, limit=600):
    text = json.dumps(result, indent=2, default=str, ensure_ascii=False)
    if len(text) > limit:
        text = text[:limit] + "\n... [truncated]"
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the 5 demo queries.")
    parser.add_argument("--index", default=None, help="Index JSONL to use.")
    parser.add_argument("--synthetic", action="store_true",
                        help="Force the synthetic fallback (tests the abstain path).")
    args = parser.parse_args(argv)

    default_index = PROJECT_ROOT / "data" / "index.jsonl"
    mode = "REAL"
    index_path = args.index or (str(default_index) if default_index.exists() else None)
    tmpdir = None
    if args.synthetic:
        mode = "DEMO SYNTHETIC"
        tmpdir = tempfile.mkdtemp(prefix="tariff-demo-")
        index_path = str(Path(tmpdir) / "synthetic-index.jsonl")
        write_chunks(index_path, synthetic_chunks())
    elif index_path is None:
        tmpdir = tempfile.mkdtemp(prefix="tariff-demo-")
        sample = build_sample_index(tmpdir)
        if sample is not None:
            mode = "SAMPLE (first pages only — queries may abstain)"
            index_path = sample
        else:
            mode = "DEMO SYNTHETIC"
            index_path = str(Path(tmpdir) / "synthetic-index.jsonl")
            write_chunks(index_path, synthetic_chunks())

    print(f"=== Tariff RAG demo — index mode: {mode} ===")
    print(f"=== Index: {index_path} ===")
    if mode.startswith("DEMO SYNTHETIC"):
        print(f"=== {SYNTHETIC_BANNER} ===")
    print(f"=== Health: {_shorten(pipeline.health_info(index_path), 800)} ===\n")

    for query in QUERIES:
        print(f"--- {query['label']} ---")
        try:
            if query["kind"] == "calculate":
                result = pipeline.run_calculate(query["payload"], index_path=index_path)
            else:
                result = pipeline.run_rag_query(query["payload"], index_path=index_path)
        except Exception as exc:  # Demo must not crash on one bad query.
            print(f"ERROR (generic, no detail leaked): {type(exc).__name__}")
            continue
        print(f"computable/abstained: computable={result.get('computable')} "
              f"abstained={result.get('abstained')}")
        print(f"citations: {json.dumps(result.get('citations'), default=str)}")
        print(f"warnings: {json.dumps(result.get('warnings'), default=str)}")
        if "breakdown" in result:
            print(f"breakdown: {_shorten(result.get('breakdown'), 1200)}")
        if "total_duty" in result:
            print(f"total_duty: {result.get('total_duty')}")
        if "answer" in result:
            # Print the FULL answer: truncating here previously cut off
            # the Flags section (e.g. the GSP-lapsed flag) in demo output.
            print(f"answer: {str(result.get('answer'))}")
        print(f"disclaimer: {result.get('disclaimer')}")
        if mode.startswith("DEMO SYNTHETIC"):
            print(f"[{SYNTHETIC_BANNER}]")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
