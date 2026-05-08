FROM python:3.10-slim

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libstdc++6 \
        curl && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Install Python dependencies (separate layer for caching)
COPY backend/requirements.txt /app/backend/requirements.txt
RUN python -m venv /app/venv && \
    /app/venv/bin/pip install --upgrade pip setuptools wheel && \
    /app/venv/bin/pip install -r /app/backend/requirements.txt

# Copy application code
COPY . .

# Switch to non-root user
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-5000}/v1/health || exit 1

# Run gunicorn from the backend directory
CMD cd /app/backend && /app/venv/bin/gunicorn --bind 0.0.0.0:${PORT:-5000} app:app
