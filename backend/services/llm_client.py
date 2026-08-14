"""
OpenAI-compatible chat-completion client for LLM semantic ranking.

DeepSeek (deepseek-chat, deepseek-v4-pro, etc.) and most hosted models expose
an OpenAI-compatible "POST /chat/completions" endpoint. This module is a
minimal standard-library client for that contract, used to let the LLM itself
understand a product description and rank tariff candidates.

It is separate from embedding.py because many LLM providers (including
DeepSeek) offer chat completions but NOT a dedicated embeddings endpoint.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import List, Optional

from backend.config import logger

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"


class LLMError(RuntimeError):
    """Raised when an LLM request cannot be fulfilled or parsed."""


class LLMClient:
    """Minimal OpenAI-compatible chat-completions client."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        timeout: float = 60.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @property
    def name(self) -> str:
        return f"llm({self.model})"

    def complete(
        self,
        messages: List[dict],
        temperature: float = 0.0,
        max_tokens: int = 512,
    ) -> str:
        """Send a chat request and return the assistant text."""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
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
            raise LLMError(f"LLM request failed: {exc}") from exc

        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected LLM response: {exc}") from exc

    def complete_json(self, system: str, user: str) -> dict:
        """Send a chat request and parse the assistant reply as a JSON object."""
        content = self.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )
        return _parse_json_object(content)


def _parse_json_object(content: str) -> dict:
    """Parse a JSON object from an LLM reply, tolerating markdown fences."""
    text = (content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise LLMError(f"LLM did not return JSON: {content!r}")
        data = json.loads(text[start:end + 1])

    if not isinstance(data, dict):
        raise LLMError(f"LLM returned a non-object: {content!r}")
    return data


def get_llm_client() -> Optional[LLMClient]:
    """Build an LLM client from environment variables.

    Set LLM_PROVIDER to enable (e.g. deepseek). Disabled by default so the API
    still runs free with the lexical fallback.
    """
    provider = os.getenv("LLM_PROVIDER", "none").strip().lower()
    if provider in ("none", "", "off", "false", "0"):
        return None

    base_url = os.getenv("LLM_API_URL", DEFAULT_BASE_URL)
    api_key = (
        os.getenv("LLM_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )
    model = os.getenv("LLM_MODEL", DEFAULT_MODEL)
    timeout = float(os.getenv("LLM_TIMEOUT", "60"))

    if not api_key:
        logger.warning(
            "LLM_PROVIDER=%s but no LLM_API_KEY/DEEPSEEK_API_KEY is set - "
            "LLM ranking disabled",
            provider,
        )
        return None

    if provider not in ("deepseek", "openai", "openai-compatible", "compatible", "llm"):
        logger.warning(
            "Unknown LLM_PROVIDER=%r - LLM ranking disabled",
            provider,
        )
        return None

    logger.info("LLM ranking enabled (model=%s)", model)
    return LLMClient(base_url=base_url, api_key=api_key, model=model, timeout=timeout)
