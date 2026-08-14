"""
LLM embedding provider for semantic product matching.

Semantic matching is powered by text embeddings produced by an LLM/transformer
model rather than lexical TF-IDF. The provider speaks the OpenAI-compatible
"POST /v1/embeddings" contract, which is implemented by OpenAI, Azure OpenAI,
Together AI, Groq, OpenRouter, Mistral, Ollama, LM Studio and most self-hosted
inference servers - so the same code can target a paid API, a free tier, or a
fully local (free) model by changing environment variables only.

Only the Python standard library is used for the HTTP call, keeping the
runtime image small and cold starts fast.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import List, Optional

from backend.config import logger

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "text-embedding-3-small"


class EmbeddingError(RuntimeError):
    """Raised when an embedding provider cannot fulfil a request."""


class EmbeddingProvider:
    """Minimal interface for a text-embedding provider."""

    def __init__(self, model: str, dimensions: Optional[int] = None):
        self.model = model
        self.dimensions = dimensions

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Embed a list of texts and return one vector per input, in order."""
        raise NotImplementedError

    @property
    def name(self) -> str:
        return self.__class__.__name__


class OpenAICompatibleProvider(EmbeddingProvider):
    """Calls any OpenAI-compatible "POST /v1/embeddings" endpoint."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        timeout: float = 30.0,
        dimensions: Optional[int] = None,
    ):
        super().__init__(model, dimensions)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @property
    def name(self) -> str:
        return f"openai-compatible({self.model})"

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        payload: dict = {"model": self.model, "input": texts}
        if self.dimensions:
            payload["dimensions"] = self.dimensions

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        request = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            json.JSONDecodeError,
        ) as exc:
            raise EmbeddingError(f"embedding request failed: {exc}") from exc

        try:
            items = sorted(body["data"], key=lambda item: item.get("index", 0))
            vectors = [item["embedding"] for item in items]
        except (KeyError, TypeError) as exc:
            raise EmbeddingError(f"unexpected embedding response: {exc}") from exc

        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"expected {len(texts)} embeddings, got {len(vectors)}"
            )
        return vectors


def get_embedding_provider() -> Optional[EmbeddingProvider]:
    """Build an embedding provider from environment variables.

    Set EMBEDDING_PROVIDER=none (default) to disable semantic matching and use
    the free lexical fallback. Any other recognised value enables it.
    """
    provider = os.getenv("EMBEDDING_PROVIDER", "none").strip().lower()

    if provider in ("none", "", "off", "false", "0"):
        logger.info("Embedding provider disabled - using lexical fallback matching")
        return None

    base_url = os.getenv("EMBEDDING_API_URL", DEFAULT_BASE_URL)
    api_key = os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY")
    model = os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL)
    timeout = float(os.getenv("EMBEDDING_TIMEOUT", "30"))
    dims = os.getenv("EMBEDDING_DIMENSIONS")
    dimensions = int(dims) if dims else None

    if provider in ("openai", "openai-compatible", "compatible", "ollama", "local"):
        if not api_key and "api.openai.com" in base_url:
            logger.warning(
                "EMBEDDING_PROVIDER=%s but no API key is set - semantic matching will be skipped",
                provider,
            )
        return OpenAICompatibleProvider(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=timeout,
            dimensions=dimensions,
        )

    logger.warning(
        "Unknown EMBEDDING_PROVIDER=%r - falling back to lexical matching",
        provider,
    )
    return None
