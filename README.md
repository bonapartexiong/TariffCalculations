# Tariff Calculation API

Customs duty calculator that matches a user's product description against the full
US Harmonized Tariff Schedule (~31,000 lines) and estimates import duties and fees.

Product matching now uses **LLM semantic understanding** (a chat LLM such as DeepSeek
that ranks candidates, or text embeddings) instead of the old TF-IDF + cosine-similarity
approach, with a dependency-free lexical fallback so the API still runs for free when
no LLM key is configured. The stack is also much cheaper to host: the frontend is a
static export and the backend runs as lightweight serverless functions (or a tiny
container).

---

## Highlights

- **LLM semantic matching** - a chat LLM (DeepSeek and other OpenAI-compatible
  models) reads the top lexical candidates and picks the best match by meaning; an
  embeddings path is also available. This captures meaning ("sneakers" ~ "athletic
  footwear"), not just shared words.
- **Free-mode fallback** - a pure-Python TF-IDF matcher (stopword removal + light
  stemming) keeps the API fully functional with no API key, no network, and no NumPy.
- **Cheap/free to host** - the frontend is a static export; the backend can run as
  Netlify Functions (free tier) with the frontend on the same domain, or as a slim
  Flask app on any free-tier host.
- **Small footprint** - pandas, scikit-learn, and openpyxl were removed. The tariff
  data is pre-built to a ~0.75 MB gzipped JSON, so cold starts are fast.
- **Optional persistence** - calculation logging defaults to stdout; file and Supabase
  backends are opt-in.

---

## Architecture

```
Browser (React/Next.js, static export)
        |
        |  POST /v1/calculate   (same-origin)
        v
Netlify redirect / .netlify/functions/calculate   <-- or Flask on any host
        |
        |  validate -> LLM semantic match -> fee calculator
        v
backend/services/
    llm_client.py       OpenAI-compatible /chat/completions client (stdlib HTTP)
    embedding.py        OpenAI-compatible /v1/embeddings client (stdlib HTTP)
    semantic_matcher.py LLM ranking + embedding search + TF-IDF fallback
    pipeline.py         shared validate -> match -> calculate pipeline
    fee_calculator.py   duty / MPF / HMF (Decimal arithmetic)
    calculation_logger.py  stdout | file | supabase | none
```

---

## Quick Start

### 1. Build the dataset (first time only)

The repository already ships `backend/data/tariffs.json.gz`. To regenerate it after
changing `tariffs.xlsx`:

```bash
python scripts/build_tariff_data.py
```

### 2. (Optional) Enable LLM semantic matching

The recommended path is a **chat LLM (DeepSeek)**. It ranks the top lexical candidates
on the fly, so there is no index to precompute - just set environment variables:

```bash
export LLM_PROVIDER=deepseek
export LLM_API_URL=https://api.deepseek.com/v1   # or your provider's base URL
export LLM_API_KEY=sk-...
export LLM_MODEL=deepseek-v4-pro                  # or deepseek-chat
```

Alternatively, the **embeddings** path needs a precomputed index:

```bash
export EMBEDDING_PROVIDER=openai
export EMBEDDING_API_KEY=sk-...
python scripts/build_embeddings.py
```

Without any LLM configuration, the API automatically uses the lexical fallback.

### 3. Run the Flask backend locally

```bash
python3.10 -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r backend/requirements.txt
cp .env.example .env                 # edit as needed
cd backend && python app.py          # -> http://localhost:5000
```

### 4. Run the frontend locally

```bash
cd TariffUI
npm install
cp .env.example .env.local           # set NEXT_PUBLIC_API_URL=http://localhost:5000
npm run dev                          # -> http://localhost:3000
```

### 5. Run the tests

```bash
python tests/test_core.py            # core matching + fee pipeline
python tests/test_netlify_handlers.py # serverless adapter contract
```

---

## How matching works

`SemanticMatcher` resolves a description in up to four steps:

1. **HTS code fast path** - if the input is a code such as `4202.11`, it is matched
   exactly (or by prefix) against `hts_number`.
2. **LLM semantic ranking** (primary) - the top `LLM_CANDIDATES` lexical matches are
   sent to a chat LLM (DeepSeek or any OpenAI-compatible model), which reads them and
   returns the best index plus a confidence. This is the path used when
   `LLM_PROVIDER` is set; results are tagged `match_source="llm"`.
3. **Embedding match** (alternative) - the query is embedded and cosine-scored against
   precomputed corpus embeddings; results are tagged `match_source="llm_embeddings"`.
4. **Lexical fallback** - if no LLM is configured (or the LLM call fails), a pure-Python
   TF-IDF + cosine matcher answers with `match_source="lexical_tfidf"`.

The response reports the engine that answered via `match_source`, so callers can tell
semantic matches apart from fallbacks.

### LLM provider (chat)

DeepSeek exposes an OpenAI-compatible `/chat/completions` endpoint (it has no
embeddings endpoint), which is why the primary path is ranking rather than embedding.
Any OpenAI-compatible chat API works the same way:

```bash
LLM_PROVIDER=deepseek
LLM_API_URL=https://api.deepseek.com/v1
LLM_API_KEY=sk-...
LLM_MODEL=deepseek-v4-pro
```

### Embedding provider (alternative)

For providers that do offer embeddings, any OpenAI-compatible endpoint works - OpenAI,
Azure OpenAI, Together, Groq, Mistral, Ollama, or a self-hosted server:

```bash
EMBEDDING_PROVIDER=openai
EMBEDDING_API_URL=http://localhost:11434/v1   # e.g. Ollama
EMBEDDING_MODEL=nomic-embed-text
```

---

## Free / cheap hosting

### Option A - everything on Netlify (free tier, one domain)

The frontend is a static export (`TariffUI/out`) and the backend is exposed as
Netlify Functions under `netlify/functions/`. `netlify.toml` rewrites `/v1/*` to the
functions, so the frontend calls the same-origin API with no CORS and no separate
server.

1. Push the repo to GitHub and connect it in Netlify.
2. (Optional) enable the DeepSeek LLM by adding these **Netlify environment variables**:
   `LLM_PROVIDER=deepseek`, `LLM_API_URL`, `LLM_API_KEY`, and `LLM_MODEL=deepseek-v4-pro`.
   No index file is needed - the LLM ranks lexical candidates on the fly.
3. Deploy - `netlify.toml` handles the build, the `[functions]` config, and the
   `/v1/*` rewrites.

### Option B - static frontend + serverless/Flask backend

Because the frontend is a static export, host it free on Netlify, GitHub Pages, or
Cloudflare Pages. Run the slim Flask backend (or the same Netlify functions) wherever
you like:

- **Render** - free web service (`gunicorn app:app`, Python 3.10).
- **Fly.io** - small allowance; `docker build .`.
- **Railway / any PaaS** - `Procfile` and `nixpacks.toml` are included.

The backend now starts in a couple of seconds and uses a few tens of MB (vs. the old
pandas/scikit-learn stack), so it fits comfortably on free tiers.

---

## Environment variables

### Backend

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_PROVIDER` | `none` (off) or `deepseek` / OpenAI-compatible chat LLM | `none` |
| `LLM_API_URL` | Chat endpoint base URL | `https://api.deepseek.com/v1` |
| `LLM_API_KEY` | API key (falls back to `DEEPSEEK_API_KEY`, then `OPENAI_API_KEY`) | - |
| `LLM_MODEL` | Chat model id (e.g. `deepseek-v4-pro`, `deepseek-chat`) | `deepseek-chat` |
| `LLM_CANDIDATES` | Number of lexical candidates the LLM ranks | `10` |
| `LLM_TIMEOUT` | LLM request timeout (seconds) | `60` |
| `EMBEDDING_PROVIDER` | `none` (off) or an OpenAI-compatible embedding provider | `none` |
| `EMBEDDING_API_URL` | Embedding endpoint base URL | `https://api.openai.com/v1` |
| `EMBEDDING_API_KEY` | API key (falls back to `OPENAI_API_KEY`) | - |
| `EMBEDDING_MODEL` | Embedding model name | `text-embedding-3-small` |
| `EMBEDDING_DIMENSIONS` | Optional vector dimension override | - |
| `EMBEDDING_LAZY_BUILD` | Build the index at startup (`true`/`false`) | `false` |
| `LOGGING_BACKEND` | `stdout` / `file` / `supabase` / `none` | `stdout` |
| `LOGGING_FILE_PATH` | JSONL path when `LOGGING_BACKEND=file` | `calculations.jsonl` |
| `SUPABASE_URL` / `SUPABASE_KEY` | Optional Supabase persistence | - |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins | `http://localhost:3000` |
| `PORT` / `FLASK_DEBUG` | Flask listen port / debug mode | `5000` / `false` |

### Frontend (`TariffUI/.env.local`)

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_API_URL` | Backend base URL; leave empty for same-origin (Netlify functions) |

---

## API

### Health

```http
GET /v1/health
```

Returns service status plus the active matching engine and model.

### Calculate

```http
POST /v1/calculate
Content-Type: application/json

{"description": "Leather handbag", "value": 500.00}
```

```json
{
  "matched_description": "Handbags...; With outer surface of leather...",
  "matched_group": "...",
  "hts_number": "4202.21.30.00",
  "match_source": "llm",
  "confidence": 0.62,
  "tariff": 0.053,
  "duty": 26.5,
  "merchandise_processing_fee": 1.73,
  "harbor_maintenance_fee": 0.63,
  "subtotal": 28.86,
  "footnote": "...",
  "request_id": "..."
}
```

Request body is limited to 1 MB; rate limits are per-IP (in-memory).

---

## Data pipeline

- `scripts/build_tariff_data.py` - reads `tariffs.xlsx` with only the standard library
  and emits `backend/data/tariffs.json` + `tariffs.json.gz`. It also normalizes tariff
  rates and derives a concise, self-contained `search_text` from the specific tariff
  line (not the huge shared chapter heading), which improves both lexical and semantic
  matching.
- `scripts/build_embeddings.py` - embeds every tariff line with the configured LLM and
  writes `embeddings.npz` / `embeddings.json` + `embeddings_meta.json`. A corpus
  fingerprint in the metadata makes stale caches self-invalidating.

---

## Project structure

```
├── backend/
│   ├── app.py                   # Flask application factory
│   ├── config.py                # Constants, thresholds, logging
│   ├── models.py                # ProductMatch, FeeBreakdown dataclasses
│   ├── validators.py            # Input validation
│   ├── routes/                  # Flask routes (health, calculate)
│   ├── services/
│   │   ├── llm_client.py        # chat LLM client (DeepSeek / OpenAI-compatible)
│   │   ├── embedding.py         # LLM embedding provider (OpenAI-compatible)
│   │   ├── semantic_matcher.py  # LLM ranking + embedding search + TF-IDF fallback
│   │   ├── pipeline.py          # shared calculate pipeline
│   │   ├── tariff_data.py       # dataset loading
│   │   ├── fee_calculator.py    # duty/MPF/HMF
│   │   └── calculation_logger.py
│   └── data/tariffs.json.gz     # pre-built dataset (committed)
├── netlify/functions/           # serverless backend (calculate, health)
├── scripts/                     # data + embedding build scripts
├── TariffUI/                    # Next.js frontend (static export)
├── tests/                       # stdlib-only smoke tests
├── netlify.toml                 # static export + functions + rewrites
├── Dockerfile / Procfile / nixpacks.toml   # container/PaaS options
└── tariffs.xlsx                 # source tariff data
```

---

## Troubleshooting

- **"Tariff dataset not found"** - run `python scripts/build_tariff_data.py`.
- **LLM matching not active** - verify `LLM_PROVIDER`, `LLM_API_KEY`, and `LLM_MODEL`.
  The health endpoint reports the active engine and model; it should show
  `"engine": "llm"` when configured correctly. (For the embeddings path, verify
  `EMBEDDING_PROVIDER` and run `python scripts/build_embeddings.py`.)
- **Frontend can't reach the API** - set `NEXT_PUBLIC_API_URL` (separate backend) or
  leave it empty and confirm the Netlify functions and `/v1/*` rewrites are deployed.
- **Rate limit exceeded** - wait for the per-IP window to reset.
