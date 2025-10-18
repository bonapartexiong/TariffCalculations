# Deployment Guide

## Overview
This guide covers deploying the refactored Tariff Calculation API to production.

## Prerequisites
- Python 3.9+
- Supabase account with `Calculations` table set up
- `tariffs.xlsx` file in the project root

## Supabase Table Schema

Create a table named `Calculations` with the following schema:

```sql
CREATE TABLE Calculations (
  id BIGSERIAL PRIMARY KEY,
  description TEXT NOT NULL,
  value DECIMAL(15, 2) NOT NULL,
  request_id UUID NOT NULL,
  timestamp TIMESTAMPTZ DEFAULT NOW(),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Add index for faster queries
CREATE INDEX idx_calculations_timestamp ON Calculations(timestamp);
CREATE INDEX idx_calculations_request_id ON Calculations(request_id);
```

## Local Development

1. **Clone the repository**
```bash
git clone https://github.com/bonapartexiong/TariffCalculations.git
cd TariffCalculations
```

2. **Create virtual environment**
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. **Install dependencies**
```bash
pip install -r requirements.txt
```

4. **Configure environment variables**
```bash
cp .env.example .env
# Edit .env with your actual values
```

5. **Run the application**
```bash
python app.py
```

The API will be available at `http://localhost:5000`

## Production Deployment (Railway)

### Method 1: Railway CLI

1. **Install Railway CLI**
```bash
npm i -g @railway/cli
```

2. **Login to Railway**
```bash
railway login
```

3. **Initialize project**
```bash
railway init
```

4. **Set environment variables**
```bash
railway variables set SUPABASE_URL="your-url"
railway variables set SUPABASE_KEY="your-key"
railway variables set ALLOWED_ORIGINS="https://yourdomain.com"
railway variables set FLASK_DEBUG="False"
```

5. **Deploy**
```bash
railway up
```

### Method 2: GitHub Integration

1. Connect your GitHub repository to Railway
2. Set environment variables in Railway dashboard
3. Railway will automatically deploy on push to main branch

## Procfile for Railway

Create a `Procfile` in your project root:

```
web: gunicorn -w 4 -b 0.0.0.0:$PORT app:app --timeout 120 --log-level info
```

## Environment Variables

Required environment variables:

| Variable | Description | Example |
|----------|-------------|---------|
| `SUPABASE_URL` | Your Supabase project URL | `https://xxx.supabase.co` |
| `SUPABASE_KEY` | Your Supabase anon/public key | `eyJhbGc...` |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins | `https://yoursite.com` |
| `PORT` | Port to run on (set by Railway) | `5000` |
| `FLASK_DEBUG` | Enable debug mode (dev only) | `False` |

## API Endpoints

### Health Check
```http
GET /v1/health
```

Response:
```json
{
  "status": "healthy",
  "version": "v1",
  "timestamp": "2025-10-18T12:00:00Z",
  "checks": {
    "tariff_data": true,
    "ml_model": true,
    "supabase": true
  }
}
```

### Calculate Tariff
```http
POST /v1/calculate
Content-Type: application/json

{
  "description": "Leather handbag",
  "value": 500.00
}
```

Response:
```json
{
  "matched_description": "Handbags, with outer surface of leather",
  "confidence": 0.856,
  "tariff": 0.10,
  "duty": 50.00,
  "merchandise_processing_fee": 1.73,
  "harbor_maintenance_fee": 0.63,
  "subtotal": 52.36,
  "footnote": "For precise tariff quotations...",
  "request_id": "123e4567-e89b-12d3-a456-426614174000"
}
```

### Rate Limits
- 100 requests per hour per IP
- 20 requests per minute per IP
- 10 requests per minute for `/v1/calculate` endpoint

## Monitoring

### Key Metrics to Monitor
- Request rate and latency
- Error rates (4xx, 5xx)
- Supabase connection failures
- ML model inference time
- Memory usage

### Recommended Tools
- Railway built-in metrics
- Supabase dashboard for database monitoring
- Sentry for error tracking
- Prometheus + Grafana for custom metrics

## Security Checklist

- ✅ Rate limiting enabled
- ✅ CORS restricted to specific origins
- ✅ HTTPS enforced (via Railway)
- ✅ Input validation and sanitization
- ✅ Security headers added
- ✅ Environment variables secured
- ✅ Debug mode disabled in production
- ✅ Structured logging (no sensitive data)

## Troubleshooting

### Issue: "Tariff file not found"
**Solution**: Ensure `tariffs.xlsx` is in the same directory as `app.py`

### Issue: "Supabase connection failed"
**Solution**: 
1. Verify `SUPABASE_URL` and `SUPABASE_KEY` are correct
2. Check that `Calculations` table exists
3. Verify Supabase project is not paused

### Issue: "Rate limit exceeded"
**Solution**: This is expected behavior. Wait for the rate limit window to reset or contact admin to increase limits.

### Issue: "Low confidence match"
**Solution**: User needs to provide more specific product description. This is working as intended.

## Updating Tariff Data

1. Update `tariffs.xlsx` file
2. Validate data format (Description and Tariff columns)
3. Redeploy the application
4. The new data will be loaded on startup

**Future improvement**: Move tariff data to Supabase for hot-reloading without redeployment.

## Performance Optimization

Current implementation handles ~100 requests/hour per instance. For higher load:

1. **Horizontal Scaling**: Add more Railway instances
2. **Caching**: Implement Redis for common queries
3. **Database**: Move tariffs to Supabase for better scalability
4. **CDN**: Use CDN for static assets if frontend served from same domain

## Rollback Procedure

If deployment fails:

1. **Railway Dashboard**: Click "Rollback" to previous deployment
2. **CLI**: `railway rollback`
3. **GitHub**: Revert the commit and push

## Support

For issues or questions:
- Email: BonaparteXiongBo@gmail.com
- GitHub Issues: https://github.com/bonapartexiong/TariffCalculations/issues
