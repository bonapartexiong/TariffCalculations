FROM python:3.10-slim

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends libstdc++6 && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy ALL files
COPY . .

# Install Python dependencies
RUN python -m venv /app/venv && \
    /app/venv/bin/pip install --upgrade pip setuptools wheel && \
    /app/venv/bin/pip install -r backend/requirements.txt

# Start command
CMD ["/app/venv/bin/gunicorn", "--bind", "0.0.0.0:$PORT", "app:app"]