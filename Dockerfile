# Start from a Python base image
FROM python:3.10-slim

# Install system dependencies (including libstdc++6)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy application files
COPY . .

# Install Python dependencies
RUN python -m venv /app/venv
RUN /app/venv/bin/pip install --upgrade pip setuptools wheel
RUN /app/venv/bin/pip install -r backend/requirements.txt

# Make start.sh executable
RUN chmod +x /app/start.sh

# Run the application
CMD ["/app/start.sh"]