#!/bin/bash
set -e

export GCC_LIB_PATH=$(find /nix/store -wholename "*/gcc-*/lib" -type d -print -quit)
export LD_LIBRARY_PATH="$GCC_LIB_PATH:$LD_LIBRARY_PATH"

source /app/venv/bin/activate
cd backend
exec gunicorn app:app --bind 0.0.0.0:$PORT