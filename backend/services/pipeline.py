"""Shared pipeline (single source of truth) for Module C.

Both the Flask routes (``backend/routes/*``) and the Netlify adapters
(``netlify/functions/*``) call into this module and nowhere else. There
is no duplicated calculation / retrieval / answering logic.

All imports of the other builders' modules (``rag.retrieval``,
``rag.calculator``, ``rag.answer``, ``rag.rates``) are defensive:
if a module or the index is missing, the pipeline degrades gracefully
(``computable: False`` / abstention + warnings) instead of crashing or
inventing a rate.

Nothing in this module ever leaks exception detail: internal errors are
logged server-side and surfaced as generic messages / warnings.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from backend.config import PROJECT_ROOT, fee_config, get_settings
from backend.exceptions import ApiError, ValidationError
from backend.validators import clamp_confidence, validate_calculate, validate_rag_query

log = logging.getLogger(__name__)

REVISION = "2026 Rev 21"
PUBLICATION = "Pub. 5802"
PUBLISHED = "2026-10-09"
SOURCE_PDF = "2026HTSRev21_0_cgh9.pdf"

DISCLAIMER = (
    "Estimate only — not a binding classification. "
    "Verify with CBP/broker. "
    "Sources: HTS 2026 Rev 21 + CBP fee config (external)."
)

# ---------------------------------------------------------------------------
# Defensive imports of sibling builders' modules.
# ---------------------------------------------------------------------------


def _optional_import(module_name, *names):
    """Import names from a module; return None for anything unavailable."""
    try:
        import importlib

        module = importlib.import_module(module_name)
    except Exception as exc:  # Module missing, broken, or Flask-less env.
        log.debug("Optional module %s unavailable: %s", module_name, exc)
        return {name: None for name in names}
    result = {}
    for name in names:
        result[name] = getattr(module, name, None)
    return result


def _module_status():
    retrieval = _optional_import("rag.retrieval", "Index")
    calculator = _optional_import("rag.calculator", "calculate")
    answer = _optional_import("rag.answer", "answer_question")
    rates = _optional_import("rag.rates", "parse_rate", "parse_special")
    return {
        "retrieval": retrieval["Index"] is not None,
        "calculator": calculator["calculate"] is not None,
        "answer": answer["answer_question"] is not None,
        "rates": rates["parse_rate"] is not None,
    }


# ---------------------------------------------------------------------------
# Index loading (cached by path + mtime + size).
# ---------------------------------------------------------------------------

_index_cache = {}


def _resolve_index_path(index_path=None):
    if index_path:
        return str(index_path)
    return get_settings().index_path


def get_index(index_path=None):
    """Return the retrieval Index, or None if unavailable / missing file."""
    path = _resolve_index_path(index_path)
    try:
        stat = os.stat(path)
        cache_key = (path, stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None
    if cache_key in _index_cache:
        return _index_cache[cache_key]
    retrieval = _optional_import("rag.retrieval", "Index")
    index_cls = retrieval["Index"]
    if index_cls is None:
        return None
    index = None
    # CONTRACTS: Index.load(path) / build(chunks). load() may be an
    # instance method (Index().load(path)) or a classmethod
    # (Index.load(path)); try both, never Index(path) (that would
    # misinterpret the path string as a chunk list).
    for factory in (lambda: index_cls().load(path),
                    lambda: index_cls.load(path)):
        try:
            candidate = factory()
        except Exception as exc:
            log.debug("Index factory failed: %s", exc)
            continue
        if candidate is not None and hasattr(candidate, "search"):
            index = candidate
            break
    if index is None:
        return None
    # Keep the cache small: one entry per path.
    _index_cache.clear()
    _index_cache[cache_key] = index
    return index


def load_meta(index_path=None):
    """Read meta.json next to the index, or {} if absent/unparseable."""
    path = Path(_resolve_index_path(index_path))
    # Prefer the meta file written next to this exact index
    # (index.meta.json); fall back to shared meta.json, which may be
    # stale if a sample build overwrote it after the full build.
    stem_meta = path.with_suffix(".meta.json")
    meta_path = stem_meta if stem_meta.exists() else path.parent / "meta.json"
    try:
        with open(meta_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _count_index_lines(index_path=None):
    path = _resolve_index_path(index_path)
    try:
        count = 0
        with open(path, "r", encoding="utf-8") as handle:
            for _ in handle:
                count += 1
        return count
    except OSError:
        return 0


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def health_info(index_path=None):
    """Build the GET /v1/health payload. Never raises on missing files."""
    path = _resolve_index_path(index_path)
    meta = load_meta(path)
    status = _module_status()
    missing = [name for name, ok in status.items() if not ok]
    # Count the actual index file when it exists: meta.json may be
    # stale (e.g. a later --sample build overwrote it). Meta is only a
    # fallback when the index file is missing.
    if os.path.exists(path):
        chunks = _count_index_lines(path)
    else:
        chunks = meta.get("chunk_count")
    engine = "rag.retrieval+rag.calculator+rag.answer"
    if missing:
        engine = "degraded (missing: " + ", ".join(missing) + ")"
    return {
        "status": "ok" if not missing else "degraded",
        "revision": meta.get("revision", REVISION),
        "publication": meta.get("publication", PUBLICATION),
        "chunks": chunks,
        "source_pdf_sha256": meta.get("source_pdf_sha256"),
        "build_date": meta.get("build_date"),
        "engine": engine,
        "index_path": path,
        "index_exists": os.path.exists(path),
        "disclaimer": DISCLAIMER,
    }


# ---------------------------------------------------------------------------
# Retrieval helpers
# ---------------------------------------------------------------------------


def _chunk_get(chunk, key, default=None):
    if isinstance(chunk, dict):
        return chunk.get(key, default)
    return getattr(chunk, key, default)


def _format_citation(chunk):
    """Format one citation as (HTS, p.N, Rev 21) plus structured fields.

    Synthetic demo chunks must never produce a real-looking citation.
    """
    if isinstance(chunk, dict) and chunk.get("synthetic"):
        return {"hts": _chunk_get(chunk, "hts"), "page": None,
                "citation": "(DEMO SYNTHETIC — no real citation)",
                "revision": REVISION, "synthetic": True}
    hts = _chunk_get(chunk, "hts")
    page = _chunk_get(chunk, "page")
    if hts and page is not None:
        text = f"({hts}, p.{page}, Rev 21)"
    elif hts:
        text = f"({hts}, Rev 21)"
    elif page is not None:
        text = f"(p.{page}, Rev 21)"
    else:
        text = "(Rev 21)"
    return {"hts": hts, "page": page, "citation": text, "revision": REVISION}


def _lookup_chunk(index, code):
    """Return (chunk|None, warning|None), tolerating tuple or bare returns."""
    if index is None:
        return None, None
    lookup = getattr(index, "lookup_hts", None)
    if not callable(lookup):
        return None, None
    try:
        result = lookup(code)
    except Exception as exc:
        log.warning("lookup_hts failed: %s", exc)
        return None, None
    if isinstance(result, tuple) and len(result) == 2:
        return result[0], result[1]
    return result, None


def _find_match(cleaned, index):
    """Best-effort match for a calculate request; None if not found.

    The returned dict carries an internal ``_chunk`` key (the full chunk
    dict) for the calculator; callers strip it before responding.
    """
    if index is None:
        return None
    try:
        hts_code = cleaned.get("hts_code")
        if hts_code:
            chunk, warning = _lookup_chunk(index, hts_code)
            if chunk is not None:
                return {
                    "hts": _chunk_get(chunk, "hts") or hts_code,
                    "description": _chunk_get(chunk, "description"),
                    "match_type": "exact_hts",
                    "warning": warning,
                    "citation": _format_citation(chunk),
                    "_chunk": chunk,
                }
        search = getattr(index, "search", None)
        if callable(search):
            hits = search(cleaned.get("description", ""), top_k=1) or []
            if hits:
                hit = hits[0]
                chunk = hit.get("chunk") if isinstance(hit, dict) else getattr(hit, "chunk", None)
                if chunk is not None:
                    warning = None
                    if isinstance(hit, dict):
                        warning = hit.get("warning")
                    return {
                        "hts": _chunk_get(chunk, "hts"),
                        "description": _chunk_get(chunk, "description"),
                        "match_type": hit.get("match_type") if isinstance(hit, dict) else None,
                        "warning": warning,
                        "citation": _format_citation(chunk),
                        "_chunk": chunk,
                    }
    except Exception as exc:
        log.warning("Match lookup failed: %s", exc)
    return None


def _normalise_citations(citations):
    """Coerce citations into a list of dicts; drop anything unusable."""
    if not isinstance(citations, list):
        return []
    out = []
    for item in citations:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, str):
            out.append({"citation": item, "revision": REVISION})
    return out


def _jsonable(value):
    """Convert Decimal / exotic types to JSON-safe values."""
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    return str(value)


# ---------------------------------------------------------------------------
# Calculate
# ---------------------------------------------------------------------------


def run_calculate(payload, index_path=None):
    """Validate + calculate. Returns the POST /v1/calculate payload."""
    cleaned = validate_calculate(payload)
    path = _resolve_index_path(index_path)
    index = get_index(path)
    meta = load_meta(path)
    match = _find_match(cleaned, index)
    warnings = []
    if index is None:
        warnings.append("Tariff index unavailable; calculation could not be grounded in the HTS PDF.")
    if match is not None and match.get("warning"):
        warnings.append(str(match["warning"]))

    calculator = _optional_import("rag.calculator", "calculate")
    calc_fn = calculator["calculate"]
    result = None
    if calc_fn is not None:
        calc_request = {}
        for key, value in cleaned.items():
            if isinstance(value, Decimal):
                calc_request[key] = str(value)
            elif key in ("mpf_config", "hmf_config"):
                calc_request[key] = {k: str(v) for k, v in value.items()}
            else:
                calc_request[key] = value
        # Calculator reads mpf_config {rate,min,cap} / hmf_config {rate};
        # also pass the canonical key names for forward compatibility.
        # Caller overrides (validated) win; otherwise env/default fees apply.
        fees = fee_config()

        def _pick(source, keys, default):
            for key in keys:
                if key in source and source[key] is not None:
                    return str(source[key])
            return str(default)

        mpf_in = dict(calc_request.get("mpf_config") or {})
        mpf_rate = _pick(mpf_in, ("mpf_rate", "rate"), fees["mpf_rate"])
        mpf_min = _pick(mpf_in, ("mpf_min", "min"), fees["mpf_min"])
        mpf_cap = _pick(mpf_in, ("mpf_cap", "cap"), fees["mpf_cap"])
        calc_request["mpf_config"] = {
            "rate": mpf_rate, "mpf_rate": mpf_rate,
            "min": mpf_min, "mpf_min": mpf_min,
            "cap": mpf_cap, "mpf_cap": mpf_cap,
        }
        hmf_in = dict(calc_request.get("hmf_config") or {})
        hmf_rate = _pick(hmf_in, ("hmf_rate", "rate"), fees["hmf_rate"])
        calc_request["hmf_config"] = {"rate": hmf_rate, "hmf_rate": hmf_rate}
        # The calculator computes from the matched chunk: hand it over.
        # Without this it sees an empty chunk and cannot compute.
        if match is not None and isinstance(match.get("_chunk"), dict):
            calc_request["chunk"] = match["_chunk"]
        else:
            calc_request["chunk"] = {}
        # Chapter 99 stack: resolve each bare HTS code to a dict entry
        # carrying its indexed rate + stacking rule, plus a rate mapping
        # for the calculator's bare-code path.
        if cleaned.get("chapter99_stack"):
            entries = []
            rates_map = {}
            for code in cleaned["chapter99_stack"]:
                ch99_chunk, ch99_warning = _lookup_chunk(index, code)
                # Chapter 99 lines are reference/additional-duty lines, not
                # dutiable base lines, so retrieval's "not a dutiable
                # classification line" warning does not apply to them.
                if ch99_warning and not (
                    isinstance(ch99_chunk, dict)
                    and ch99_chunk.get("kind") == "chapter99"
                ):
                    warnings.append(str(ch99_warning))
                if isinstance(ch99_chunk, dict):
                    general_raw = ch99_chunk.get("general_raw")
                    entries.append({
                        "hts": code,
                        "general_raw": general_raw,
                        "stacking": ch99_chunk.get("stacking"),
                    })
                    if general_raw:
                        rates_map[code] = general_raw
                else:
                    entries.append(code)
            calc_request["chapter99_stack"] = entries
            calc_request["chapter99_rates"] = rates_map
        try:
            result = calc_fn(calc_request)
        except ApiError:
            raise
        except Exception as exc:
            log.warning("Calculator failed: %s", exc)
            warnings.append("Calculation failed; no duty estimate is returned.")
            result = None
    else:
        warnings.append("Calculator module unavailable; no duty estimate is returned.")

    if result is None:
        result = {}
    if not isinstance(result, dict):
        result = {"breakdown": [], "warnings": ["Calculator returned an unusable result."]}

    for warning in result.get("warnings") or []:
        warnings.append(str(warning))
    citations = _normalise_citations(result.get("citations") or [])
    if match is not None and match.get("citation"):
        if match["citation"] not in citations:
            citations.insert(0, match["citation"])

    provenance = result.get("provenance") or {}
    if not isinstance(provenance, dict):
        provenance = {}
    provenance.setdefault("revision", meta.get("revision", REVISION))
    provenance.setdefault("publication", meta.get("publication", PUBLICATION))
    provenance.setdefault("source_pdf", SOURCE_PDF)
    provenance.setdefault("source_pdf_sha256", meta.get("source_pdf_sha256"))
    provenance.setdefault("index_path", path)
    provenance.setdefault("engine", "rag.calculator" if calc_fn is not None else "degraded")

    inputs_echo = result.get("inputs_echo")
    # Merge: start from the validated request echo (always has
    # description + value), then overlay the calculator's non-null echo.
    # The calculator's own echo omits description; never let it drop it.
    merged_echo = {
        "description": cleaned["description"],
        "value": str(cleaned["value"]),
    }
    for key in ("hts_code", "quantity", "quantity_unit", "origin_country",
                "program", "mode", "quota_status", "chapter99_stack", "de_minimis"):
        if key in cleaned:
            value = cleaned[key]
            merged_echo[key] = str(value) if isinstance(value, Decimal) else value
    if isinstance(inputs_echo, dict):
        for key, value in inputs_echo.items():
            # Keep the caller's representation of the stack: the
            # calculator echoes its resolved dict entries, which would
            # obscure what the caller actually asked for.
            if key == "chapter99_stack":
                continue
            if value is not None:
                merged_echo[key] = value
    inputs_echo = merged_echo

    confidence = clamp_confidence(result.get("confidence")) if "confidence" in result else None
    public_match = None
    if match is not None:
        public_match = {k: v for k, v in match.items() if k != "_chunk"}
    response = {
        "match": public_match,
        "breakdown": _jsonable(result.get("breakdown") or []),
        "total_duty": _jsonable(result.get("total_duty"))
        if result.get("total_duty") is not None else None,
        "warnings": warnings,
        "citations": citations,
        "provenance": _jsonable(provenance),
        "inputs_echo": _jsonable(inputs_echo),
        "computable": bool(result.get("computable", False)),
        "disclaimer": DISCLAIMER,
    }
    if confidence is not None:
        response["confidence"] = confidence
    return response


# ---------------------------------------------------------------------------
# RAG query
# ---------------------------------------------------------------------------


def run_rag_query(payload, index_path=None):
    """Validate + answer. Returns the POST /v1/rag/query payload."""
    cleaned = validate_rag_query(payload)
    path = _resolve_index_path(index_path)
    index = get_index(path)
    meta = load_meta(path)
    warnings = []

    answer_mod = _optional_import("rag.answer", "answer_question")
    answer_fn = answer_mod["answer_question"]
    if answer_fn is not None:
        try:
            result = answer_fn(cleaned["question"], index)
        except ApiError:
            raise
        except Exception as exc:
            log.warning("Answer module failed: %s", exc)
            warnings.append("Answer generation failed; abstaining rather than guessing.")
            result = None
        if isinstance(result, dict) and result:
            citations = _normalise_citations(result.get("citations") or [])
            chunks = result.get("chunks") or []
            if isinstance(chunks, list):
                chunks = [
                    {
                        "id": _chunk_get(c, "id"),
                        "hts": _chunk_get(c, "hts"),
                        "page": _chunk_get(c, "page"),
                        "text": str(_chunk_get(c, "text") or "")[:1000],
                    }
                    for c in chunks
                    if isinstance(c, (dict, object))
                ]
            response = {
                "answer": str(result.get("answer") or ""),
                "citations": citations,
                "chunks": chunks,
                "abstained": bool(result.get("abstained", False)),
                "warnings": [str(w) for w in (result.get("warnings") or [])] + warnings,
                "provenance": {
                    "revision": meta.get("revision", REVISION),
                    "publication": meta.get("publication", PUBLICATION),
                    "source_pdf": SOURCE_PDF,
                    "source_pdf_sha256": meta.get("source_pdf_sha256"),
                    "index_path": path,
                },
                "disclaimer": DISCLAIMER,
            }
            if "confidence" in result:
                response["confidence"] = clamp_confidence(result.get("confidence"))
            return response

    # Fallback: no answer module. Ground strictly in retrieved chunks;
    # never state a rate that was not retrieved.
    if index is None:
        return {
            "answer": "No tariff index is available, so I cannot answer from the HTS PDF. Abstaining rather than guessing.",
            "citations": [],
            "chunks": [],
            "abstained": True,
            "warnings": warnings + ["Tariff index unavailable; answer module unavailable."],
            "provenance": {
                "revision": REVISION, "publication": PUBLICATION,
                "source_pdf": SOURCE_PDF, "source_pdf_sha256": meta.get("source_pdf_sha256"),
                "index_path": path,
            },
            "disclaimer": DISCLAIMER,
        }
    try:
        hits = index.search(cleaned["question"], top_k=cleaned["top_k"]) or []
    except Exception as exc:
        log.warning("Retrieval failed: %s", exc)
        hits = []
    if not hits:
        return {
            "answer": "No matching passage was found in the HTS PDF for this question. Abstaining rather than guessing.",
            "citations": [],
            "chunks": [],
            "abstained": True,
            "warnings": warnings + ["No retrieval hits; abstained."],
            "provenance": {
                "revision": REVISION, "publication": PUBLICATION,
                "source_pdf": SOURCE_PDF, "source_pdf_sha256": meta.get("source_pdf_sha256"),
                "index_path": path,
            },
            "disclaimer": DISCLAIMER,
        }
    chunks = []
    citations = []
    for hit in hits:
        chunk = hit.get("chunk") if isinstance(hit, dict) else getattr(hit, "chunk", None)
        if chunk is None:
            continue
        chunks.append({
            "id": _chunk_get(chunk, "id"),
            "hts": _chunk_get(chunk, "hts"),
            "page": _chunk_get(chunk, "page"),
            "text": str(_chunk_get(chunk, "text") or "")[:1000],
        })
        citations.append(_format_citation(chunk))
        if isinstance(hit, dict) and hit.get("warning"):
            warnings.append(str(hit["warning"]))
    answer_text = (
        "Based on the retrieved HTS passages below, see the cited passages "
        "for the applicable text. No rate is stated here beyond what the "
        "cited passages show; verify classification with CBP/broker."
    )
    return {
        "answer": answer_text,
        "citations": citations,
        "chunks": chunks,
        "abstained": False,
        "warnings": warnings,
        "provenance": {
            "revision": REVISION, "publication": PUBLICATION,
            "source_pdf": SOURCE_PDF, "source_pdf_sha256": meta.get("source_pdf_sha256"),
            "index_path": path,
        },
        "disclaimer": DISCLAIMER,
    }


def _unused():
    # Keep linters quiet about intentionally unused imports.
    return (ValidationError, datetime, timezone)
