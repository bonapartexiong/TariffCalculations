# Tariff Calculation API

Customs duty calculation API that matches product descriptions against a tariff database
using TF-IDF vectorization and cosine similarity.

A **Next.js frontend** (`TariffUI/`) calls a **Flask backend** (`backend/`) deployed
on Railway (or standalone). The frontend is designed for deployment on **Netlify**.

---

## Quick Start

### Backend

```bash
git clone https://github.com/bonapartexiong/TariffCalculations.git
cd TariffCalculations

python3.10 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env   # edit with real Supabase credentials

cd backend && python app.py
# → http://localhost:5000
```

### Frontend

```bash
cd TariffUI
npm install
cp .env.example .env.local   # or set NEXT_PUBLIC_API_URL
npm run dev
# → http://localhost:3000
```

---

## Environment Variables

### Backend (`.env`)

| Variable | Description | Example |
|----------|-------------|---------|
| `SUPABASE_URL` | Supabase project URL | `https://xxx.supabase.co` |
| `SUPABASE_KEY` | Supabase anon/public key | `eyJhbGc...` |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins | `http://localhost:3000,https://you.netlify.app` |
| `PORT` | Port to run on | `5000` |
| `FLASK_DEBUG` | Enable debug mode (dev only) | `False` |

The app starts even if Supabase is unavailable — calculation logging is skipped until
the connection recovers.

### Frontend (`TariffUI/.env.local`)

| Variable | Description | Example |
|----------|-------------|---------|
| `NEXT_PUBLIC_API_URL` | Backend API base URL | `http://localhost:5000` or `https://your-api.railway.app` |

---

## API Endpoints

### Health Check
```http
GET /v1/health
```

```json
{
  "status": "healthy",
  "version": "v1",
  "timestamp": "2025-01-01T00:00:00Z",
  "checks": {
    "tariff_data": true,
    "ml_model": true,
    "supabase": true
  }
}
```

Returns `503` when any check fails (`"status": "degraded"`).

### Calculate Tariff
```http
POST /v1/calculate
Content-Type: application/json

{
  "description": "Leather handbag",
  "value": 500.00
}
```

Request body is limited to **1 MB**.

---

## Rate Limits

- 100 requests per hour per IP
- 20 requests per minute per IP
- 10 requests per minute for `POST /v1/calculate`

> **Production note:** rate limits use in-memory storage. For multi-worker deployments
> switch to Redis by changing `storage_uri` in `backend/app.py`. The app includes
> `ProxyFix` middleware so client IPs are correct behind CDNs and load balancers.

---

## Project Structure

```
├── backend/                  # Flask API
│   ├── app.py                # Application factory & entry point
│   ├── config.py             # Constants, logging setup
│   ├── models.py             # Dataclasses (ProductMatch, FeeBreakdown)
│   ├── exceptions.py         # Custom exception hierarchy
│   ├── validators.py         # Input validation (whitelist-based)
│   ├── middleware.py          # Request ID, security headers
│   ├── requirements.txt      # Python dependencies
│   ├── routes/
│   │   ├── health.py         # GET /v1/health
│   │   └── calculate.py      # POST /v1/calculate
│   └── services/
│       ├── tariff_data.py    # Excel loading, TF-IDF matching
│       ├── fee_calculator.py # Duty/MPF/HMF computation (Decimal)
│       └── calculation_logger.py  # Supabase persistence
├── TariffUI/                 # Next.js frontend
│   ├── pages/index.js        # Main calculator UI
│   ├── next.config.js        # Next.js configuration
│   ├── package.json          # Dependencies
│   └── .env.example          # Frontend env template
├── netlify.toml              # Netlify build configuration
├── Dockerfile                # Backend Docker image
├── Procfile                  # Railway / generic process runner
├── nixpacks.toml             # Railway Nixpacks builder
└── tariffs.xlsx              # Tariff data (Description + Tariff columns)
```

---

## Production Deployment

### Netlify (Frontend)

The frontend is configured for Netlify via `netlify.toml` and `next.config.js`.
Netlify's Essential Next.js plugin handles SSR/ISR.

1. Push to GitHub
2. Connect the repo in Netlify dashboard
3. Set `NEXT_PUBLIC_API_URL` to your backend URL in Netlify environment variables
4. Deploy

### Railway (Backend)

The `Procfile` and `nixpacks.toml` are configured for Railway.
Connect your repo and set the environment variables listed above.

### Docker (Backend)

```bash
docker build -t tariff-api .
docker run -p 5000:5000 --env-file .env tariff-api
```

### Gunicorn (standalone)

```bash
cd backend
gunicorn -w 4 -b 0.0.0.0:$PORT app:app --timeout 120 --log-level info
```

---

## Updating Tariff Data

1. Replace `tariffs.xlsx` in the project root
2. Ensure columns `Description` and `Tariff` exist with rates between 0–1
3. Redeploy — the new data loads on startup

---

## Troubleshooting

**"Module not found" on start** — make sure you're running from the `backend/`
directory, or set `PYTHONPATH` to the project root.

**Supabase connection failed** — the app starts in degraded mode. Verify your
`SUPABASE_URL` and `SUPABASE_KEY`, and check that the `Calculations` table exists.

**Frontend can't reach API** — set `NEXT_PUBLIC_API_URL` to the correct backend URL
in `TariffUI/.env.local` (local) or Netlify dashboard (production). Ensure your
backend's `ALLOWED_ORIGINS` includes the frontend domain.

**Rate limit exceeded** — wait for the rate window to reset. The limits are per-IP.
