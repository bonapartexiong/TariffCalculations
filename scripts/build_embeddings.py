"""
Precompute LLM embeddings for the tariff corpus.

Run this once (or in CI) after changing tariffs.xlsx so the API can answer
queries with LLM semantic matching without paying to re-embed the whole
corpus on every cold start.

Environment:
  EMBEDDING_PROVIDER   e.g. openai (default: none)
  EMBEDDING_API_URL    OpenAI-compatible base URL (default https://api.openai.com/v1)
  EMBEDDING_API_KEY    API key (falls back to OPENAI_API_KEY)
  EMBEDDING_MODEL      e.g. text-embedding-3-small (default)

Usage:
    python scripts/build_embeddings.py [--batch-size 500]

Outputs (in backend/data/):
    embeddings_meta.json   model, dimensions, count, corpus fingerprint
    embeddings.npz         float32 matrix (loaded by default)
    embeddings.json        portable JSON copy (used when NumPy is absent)
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
DATA_DIR = BACKEND_DIR / "data"

for _path in (str(REPO_ROOT), str(BACKEND_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from backend.services.embedding import get_embedding_provider
from backend.services.semantic_matcher import SemanticMatcher


def load_records():
    gz_path = DATA_DIR / "tariffs.json.gz"
    json_path = DATA_DIR / "tariffs.json"
    if gz_path.exists():
        with gzip.open(gz_path, "rt", encoding="utf-8") as fh:
            return json.load(fh)["records"]
    with open(json_path, "r", encoding="utf-8") as fh:
        return json.load(fh)["records"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Precompute tariff embeddings")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    provider = get_embedding_provider()
    if provider is None:
        print("No embedding provider configured (set EMBEDDING_PROVIDER and key).", file=sys.stderr)
        return 1

    records = load_records()
    matcher = SemanticMatcher(
        records=records, provider=provider, cache_dir=str(DATA_DIR)
    )
    matcher.build_index(batch_size=args.batch_size)
    print(f"Embedded {len(records)} records with model {provider.model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
