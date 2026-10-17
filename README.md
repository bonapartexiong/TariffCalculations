# TariffCalculations-RAG

Grounded tariff estimation from the Harmonized Tariff Schedule of the
United States, **2026 Rev 21 (Pub. 5802)**. The HTS PDF is the sole
ground truth. Every rate in every answer is cited as
`(HTS, p.N, Rev 21)`; anything not found in the PDF is abstained from,
never invented.

> Estimate only — not a binding classification. Verify with CBP/broker.
> Sources: HTS 2026 Rev 21 + CBP fee config (external).

## Architecture

```
HTS 2026 Rev 21 PDF (Pub. 5802, sole ground truth)
        |
        v
rag/ingest/parse_hts_pdf()          (Module A — PDF -> chunks)
        |
        v
scripts/build_index.py  -->  data/index.jsonl + data/meta.json
        |
        v
backend/services/pipeline.py  <-- SINGLE SOURCE OF TRUTH
   |  imports defensively: rag.retrieval / rag.calculator / rag.answer
   |  degrades gracefully when the index or a module is missing
   +--> backend/ (Flask): routes/calculate.py, routes/rag.py, routes/health.py
   +--> netlify/functions/ (thin adapters: calculate.py, health.py)
        |
        v
POST /v1/calculate  |  POST /v1/rag/query  |  GET /v1/health
```

Module map (see `CONTRACTS.md` for the authoritative contracts):

| Area | Owner | Notes |
| --- | --- | --- |
| `rag/ingest`, `rag/rates`, `rag/retrieval`, `rag/calculator`, `rag/answer` | Other builders | Module C imports them defensively (`try/except`), never writes them |
| `backend/`, `netlify/functions/`, `scripts/`, `tests/`, docs | **Module C (this work)** | This repo's backend + scripts + docs + demo |

Key design decisions:

- **One pipeline.** Flask routes and Netlify functions both call
  `backend.services.pipeline`. There is no duplicated calculation,
  retrieval, or answering logic.
- **Defensive imports.** If a sibling module or the index is missing,
  the API returns `computable: false` / abstention + warnings — never a
  crash, never an invented rate.
- **No exception detail leaks.** Error bodies carry only a stable
  `code` + generic `message`. No tracebacks, file paths, or payloads.
- **No personal email** in any response. No secrets in the repo.

## Grounding guarantees

1. Rates come only from PDF chunks (JSONL index) via `rag/retrieval`
   + `rag/calculator`. No rate is ever produced by an LLM from memory.
2. Every response carries `citations` (`(HTS, p.N, Rev 21)`),
   `provenance` (revision, publication, PDF SHA-256, index path), and
   the generic disclaimer.
3. `rag/answer` abstains when retrieval finds nothing, and flags
   GSP-lapsed, GN12-deleted, MPF/HMF-external, and Chapter 99 expiry.
   See `GROUNDING.md` for the full list of PDF facts and traps.
4. MPF/HMF are **external CBP fee config**, not HTS PDF data, and are
   labelled as such everywhere. Defaults: MPF 0.3464%, min $32.71,
   cap $575 (CBP FY2025); HMF 0.125%, ocean-only. Override via env
   (`MPF_RATE`, `MPF_MIN`, `MPF_CAP`, `HMF_RATE`) — never via the PDF.
5. Rounding: each component rounded HALF_UP to cents; subtotal = sum
   of rounded components. Percentages are stored as Decimal strings,
   never floats (see `AUDIT.md`: the old 100x bug).

## What is NOT in the PDF (do not claim otherwise)

- MPF/HMF rates and rules (external CBP fee config).
- Quota fill levels / in-quota availability (TRQ administration is
  external; the PDF gives the in/out rates and notes).
- Binding classification decisions, broker advice, or effective dates
  for every line (the preface date 2026-10-09 is **not** an effective
  date for all lines).
- GSP benefits as currently claimable — GSP is **lapsed**; `Free (A)`
  must be flagged, not presented as claimable. See `GROUNDING.md`.
- Any spreadsheet source: the legacy spreadsheet is **disregarded**.
  The PDF is the only ground truth.

## Build the index from the PDF

```bash
pip install -r requirements.txt        # PyMuPDF + pytest
python scripts/build_index.py \
  --pdf ~/workspace/user/files/2026HTSRev21_0_cgh9.pdf \
  --out data/index.jsonl --validate
# Sample (first pages only, smoke test):
python scripts/build_index.py --sample
# Page subset:
python scripts/build_index.py --pages 1-10 --validate
```

Output: `data/index.jsonl` (one chunk per line, per `CONTRACTS.md`)
+ `data/meta.json`
`{revision, publication, source_pdf_sha256, build_date, chunk_count, pages}`.

## Run the API

```bash
pip install -r backend/requirements.txt   # Flask, flask-cors, python-dotenv, gunicorn
cp .env.example .env                     # then set INDEX_PATH / PDF_PATH / PORT / ALLOWED_ORIGINS
python -m backend.app                    # dev server on 127.0.0.1:$PORT
# Production:
gunicorn 'backend.app:create_app()' --bind 0.0.0.0:$PORT
```

## Run the demo

```bash
python scripts/demo.py               # uses data/index.jsonl, else sample, else synthetic fallback
python scripts/demo.py --synthetic   # force the labelled abstain/uncomputable path
```

## Endpoints

### GET /v1/health

```bash
curl localhost:5000/v1/health
```

```json
{
  "status": "ok",
  "revision": "2026 Rev 21",
  "chunks": 123456,
  "source_pdf_sha256": "…",
  "build_date": "2026-10-17T…Z",
  "engine": "rag.retrieval+rag.calculator+rag.answer",
  "index_path": "…/data/index.jsonl",
  "index_exists": true
}
```

### POST /v1/calculate

Backward-compatible body is just `description` + `value`. Extended
fields: `hts_code`, `quantity`, `quantity_unit`, `origin_country`,
`program`, `mode` (`ocean|air|land|mail`), `quota_status`
(`in|out|unknown`), `chapter99_stack`, `de_minimis`, `mpf_config`,
`hmf_config`.

```bash
curl -X POST localhost:5000/v1/calculate -H 'Content-Type: application/json' -d '{
  "description": "Electric motor",
  "hts_code": "8501.51.20",
  "value": 1000,
  "origin_country": "China",
  "mode": "ocean",
  "chapter99_stack": ["9903.88.01"]
}'
```

Response: `match`, `breakdown`, `total_duty`, `warnings`, `citations`,
`provenance`, `inputs_echo`, `computable`, `disclaimer`.

Validation: `description` 3–500 chars (apostrophes, semicolons,
commas, quotes, `+`, `%` allowed); `value` 0.01–100,000,000 as a
finite Decimal (booleans, NaN, and infinities rejected).

### POST /v1/rag/query

```bash
curl -X POST localhost:5000/v1/rag/query -H 'Content-Type: application/json' -d '{
  "question": "Is Free (A) claimable under GSP right now?",
  "top_k": 5
}'
```

Response: `answer`, `citations`, `chunks`, `abstained`, `warnings`,
`provenance`, `disclaimer`. Abstains when nothing is retrieved.

## Tests

```bash
python -m pytest tests/ -q
```

Flask tests use `test_client` and skip if Flask is missing; validator,
config, Netlify-adapter, and CLI-guard tests run regardless.

## Docs

- `CONTRACTS.md` — authoritative shared contracts (all modules).
- `GROUNDING.md` — PDF facts, traps, and what must be verified in-PDF.
- `AUDIT.md` — prior-audit bugs → fixes mapping.
- `docs/` — pointer to the docs above.
