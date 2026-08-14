"""
Tariff data loading and product matching.

Loads the compact, pre-built JSON dataset (see scripts/build_tariff_data.py)
and delegates matching to SemanticMatcher, which uses a chat LLM (e.g.
DeepSeek) or embeddings for semantic understanding, with a lexical TF-IDF
fallback.
"""
from __future__ import annotations

import gzip
import json
import os
from typing import Optional, Sequence

from backend.config import logger
from backend.models import ProductMatch
from backend.services.embedding import EmbeddingProvider, get_embedding_provider
from backend.services.llm_client import LLMClient, get_llm_client
from backend.services.semantic_matcher import SemanticMatcher


def _default_data_dir() -> str:
    """Return the backend/data directory (one level above this file)."""
    services_dir = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.dirname(services_dir)
    return os.path.join(backend_dir, "data")


class TariffDataService:
    """Loads tariff records and exposes semantic product matching."""

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = data_dir or _default_data_dir()
        self.records: Sequence[dict] = []
        self.matcher: Optional[SemanticMatcher] = None
        self.provider: Optional[EmbeddingProvider] = None
        self.llm_client: Optional[LLMClient] = None

    def load_data(self) -> None:
        """Load and validate the tariff dataset from the JSON cache."""
        gz_path = os.path.join(self.data_dir, "tariffs.json.gz")
        json_path = os.path.join(self.data_dir, "tariffs.json")

        if os.path.exists(gz_path):
            with gzip.open(gz_path, "rt", encoding="utf-8") as fh:
                payload = json.load(fh)
        elif os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        else:
            raise FileNotFoundError(
                f"Tariff dataset not found. Expected {gz_path} or {json_path}. "
                "Run scripts/build_tariff_data.py to generate it.",
            )

        records = payload.get("records")
        if not records:
            raise ValueError("Tariff dataset contains no records")

        self.records = records
        self.provider = get_embedding_provider()
        self.llm_client = get_llm_client()
        llm_candidates = int(os.getenv("LLM_CANDIDATES", "10"))
        self.matcher = SemanticMatcher(
            records=self.records,
            provider=self.provider,
            cache_dir=self.data_dir,
            llm_client=self.llm_client,
            llm_candidates=llm_candidates,
        )

        if os.getenv("EMBEDDING_LAZY_BUILD", "").lower() in ("true", "1", "yes"):
            if self.provider is None:
                logger.warning("EMBEDDING_LAZY_BUILD set but no provider configured")
            else:
                logger.info("Building embedding index at startup (EMBEDDING_LAZY_BUILD)")
                self.matcher.build_index()

        logger.info(
            "Successfully loaded %d tariff records (matcher=%s)",
            len(self.records),
            self.llm_client.name
            if self.llm_client
            else (self.provider.name if self.provider else "lexical-tfidf"),
        )

    def find_match(self, description: str) -> ProductMatch:
        """Find the best matching tariff record for a description."""
        if self.matcher is None:
            raise ValueError("Tariff data not loaded")
        return self.matcher.find_match(description)
