# Shared contracts (authoritative — all modules must conform)

## Revision / provenance (every chunk, every response)
REVISION = "2026 Rev 21"
PUBLICATION = "Pub. 5802"
PUBLISHED = "2026-10-09"  # preface date; NOT an effective date for all lines
SOURCE_PDF = "2026HTSRev21_0_cgh9.pdf"
PDF pagination = PDF page numbers, 1=cover.

## Chunk dict (JSONL, one per line)
{
 "id": str,  # e.g. "hts:8501.51.20" or "gn:3-a" or "note:ch61"
 "kind": "gri|general_note|chapter_note|tariff_line|chapter99|appendix|statistical_note|section_note",
 "text": str,  # full searchable text
 "hts": str|None, "description": str|None, "unit": str|None,
 "general_raw": str|None, "special_raw": str|None, "col2_raw": str|None,
 "special_programs": [{"symbol":str,"rate_raw":str}] (may be []),
 "footnotes": [str], "page": int, "chapter": int|None, "section": str|None,
 "revision": "2026 Rev 21", "publication": "Pub. 5802",
 "effective_period": str|None, "stacking": "cumulative|in_lieu|standalone|unknown",
 "reference_only": bool, "dutiable": bool  # True only for 8-digit (or 6-digit when no 8-digit) tariff lines; False for headings/suffix rows
}

## Rate dict — rag/rates.py::parse_rate(raw:str) -> dict
{
 "kind": "free|ad_valorem|specific|compound|cross_ref|uncomputable|empty",
 "raw": str, "components": [{"value": str(Decimal), "unit": "percent|kg|doz|each|jewel|article|liter|head|gross|pair|... ", "per": str}],
 "requires_quantity": bool, "note": str,
 "cross_ref_target": str|None
}
parse_special(special_raw) -> {"programs":[{"symbol":str,"rate":RateDict}], "raw":str}
parse_rate must NEVER return float. Values as Decimal strings.

## Retrieval — rag/retrieval.py
class Index: load(path) / build(chunks)
  search(query, top_k=10) -> list[RetrievalHit]
  lookup_hts(code) -> chunk|None (normalize: strip spaces/dots handling, truncate 10-digit->8-digit, never return empty-hts, heading rows only with warning)
RetrievalHit = {"chunk": Chunk, "score": float in [0,1], "match_type": "exact_hts|prefix_hts|bm25|embedding", "warning": str|None}

## Calculator — rag/calculator.py
calculate(request: dict) -> dict with keys: breakdown, warnings, citations, provenance, inputs_echo, computable:bool
Inputs: value (Decimal str/float), quantity+quantity_unit, origin_country, program (or "auto-lowest"), mode ocean|air|land|mail, quota_status in|out|unknown, chapter99_stack [hts...], de_minimis: bool, mpf_config override, hmf_config override.
MPF/HMF defaults: MPF rate 0.3464%, min $32.71, cap $575 (CBP FY2025, EXTERNAL — not in HTS PDF, label so); HMF 0.125% ocean-only. Config in rag/fees_config.py.
Rounding: round each component HALF_UP to cents, subtotal = sum of rounded components.

## Answer — rag/answer.py
answer_question(question, index, calculator=None, llm=None) -> {"answer":str, "citations":[...], "chunks":[...], "abstained":bool, "warnings":[...]}
Rules: cite every rate as (HTS, p.N, Rev 21); abstain if no retrieval; never invent rate; flag GSP-lapsed, GN12 deleted, MPF/HMF external, Ch99 expiry.

## Backend
POST /v1/calculate {description,value,quantity,quantity_unit,origin_country,program,mode,quota_status,chapter99_stack,de_minimis}
POST /v1/rag/query {question, top_k}
GET /v1/health {status, revision, chunks, source_pdf_sha256, build_date, engine}
No personal email. Generic disclaimer.
