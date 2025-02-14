#!/bin/bash
set -e

# Install system dependencies
apt-get update
apt-get install -y libstdc++6 build-essential python3-dev

# Create lib directory if it doesn't exist
mkdir -p /usr/lib/x86_64-linux-gnu/

# Find and copy libstdc++
find / -name 'libstdc++.so.6*' -exec cp {} /usr/lib/x86_64-linux-gnu/ \;

# Update library cache
ldconfig

# Set up Python environment
python -m venv /app/venv
. /app/venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install 'numpy==1.24.4'
cd backend && pip install -r requirements.txt