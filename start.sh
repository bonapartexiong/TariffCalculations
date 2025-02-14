#!/bin/bash
set -e

# Dynamically locate GCC 12 library path
export GCC_LIB_PATH=$(find /nix/store -wholename "*/gcc-12*/lib" -type d -print -quit)
export LD_LIBRARY_PATH="$GCC_LIB_PATH:$LD_LIBRARY_PATH"

# Activate virtual environment and start the app
source /app/venv/bin/activate
cd backend
exec gunicorn app:app --bind 0.0.0.0:$PORT