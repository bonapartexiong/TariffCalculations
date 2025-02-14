#!/bin/bash
set -e

# Set up Python environment
python -m venv /app/venv
. /app/venv/bin/activate
pip install --upgrade pip setuptools wheel
cd backend && pip install -r requirements.txt