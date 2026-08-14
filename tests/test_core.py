"""
Stdlib-only smoke tests for the core pipeline (no Flask / no external deps).

Run:  python tests/test_core.py
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.services.semantic_matcher import SemanticMatcher
from backend.services.tariff_data import TariffDataService
from backend.services.fee_calculator import FeeCalculator
from backend.validators import InputValidator
from backend.exceptions import ValidationError


class FakeLLMClient:
    """Fake chat-LLM client with a configurable reply."""

    def __init__(self, index=0, confidence=0.9):
        self.model = "fake-llm"
        self._index = index
        self._confidence = confidence

    @property
    def name(self):
        return f"llm({self.model})"

    def complete_json(self, system, user):
        assert "Candidates:" in user
        return {"index": self._index, "confidence": self._confidence, "reason": "fake"}


class FakeEmbeddingProvider:
    """Deterministic token-hash embeddings (cosine ~ shared-token overlap)."""

    def __init__(self, dim: int = 256):
        self.model = "fake-embedding"
        self.dim = dim
        self._re = re.compile(r"[a-z0-9]+")

    def embed(self, texts):
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for token in set(self._re.findall(text.lower())):
                h = int(hashlib.md5(token.encode()).hexdigest(), 16) % self.dim
                vec[h] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


def test_lexical_matching(records):
    matcher = SemanticMatcher(records, provider=None)
    match = matcher.find_match("leather handbag")
    assert match.match_source == "lexical_tfidf", match.match_source
    assert 0.0 <= match.confidence <= 1.0
    print("lexical leather handbag ->", match.description[:80], "|", match.hts_number, "|", round(match.confidence, 3))

    match2 = matcher.find_match("men's cotton t-shirt")
    print("lexical t-shirt ->", match2.description[:80], "|", round(match2.confidence, 3))

    match3 = matcher.find_match("4202.11")
    assert match3.match_source == "hts_code", match3.match_source
    print("hts 4202.11 ->", match3.hts_number, "|", match3.description[:80])
    return match


def test_semantic_matching(records):
    tmp = REPO_ROOT / ".test_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        matcher = SemanticMatcher(records, provider=FakeEmbeddingProvider(), cache_dir=str(tmp))
        matcher.build_index(batch_size=1000)
        assert len(matcher.corpus_embeddings) == len(records)
        match = matcher.find_match("leather handbag")
        assert match.match_source == "llm_embeddings", match.match_source
        assert 0.0 <= match.confidence <= 1.0
        print(
            "semantic path OK (fake provider verifies plumbing only) -> source:",
            match.match_source,
            "| confidence:",
            round(match.confidence, 3),
        )
        assert os.path.exists(os.path.join(tmp, "embeddings_meta.json"))
        assert os.path.exists(os.path.join(tmp, "embeddings.json"))
        return match
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_llm_matching(records):
    matcher = SemanticMatcher(records, provider=None, llm_client=FakeLLMClient(index=0, confidence=0.9))
    match = matcher.find_match("leather handbag")
    assert match.match_source == "llm", match.match_source
    assert match.confidence == 0.9
    assert match.hts_number.startswith("4202"), match.hts_number
    print("llm leather handbag ->", match.hts_number, "|", match.description[:60], "|", match.match_source)


def test_llm_falls_back_when_non_committal(records):
    # The LLM says "no match" -> the API must degrade to lexical, not 404.
    matcher = SemanticMatcher(records, provider=None, llm_client=FakeLLMClient(index=-1, confidence=0.0))
    match = matcher.find_match("leather handbag")
    assert match.match_source == "lexical_tfidf", match.match_source
    assert match.hts_number.startswith("4202"), match.hts_number
    print("llm fallback ->", match.hts_number, "|", match.match_source)


def test_fees():
    fees = FeeCalculator.calculate_fees(500.0, 0.08, "Trunks... leather", 0.9)
    assert abs(fees.duty - 40.0) < 0.01, fees.duty
    assert abs(fees.merchandise_processing_fee - 1.73) < 0.01, fees.merchandise_processing_fee
    assert abs(fees.harbor_maintenance_fee - 0.63) < 0.01, fees.harbor_maintenance_fee
    print("fees duty/MPF/HMF/subtotal:", fees.duty, fees.merchandise_processing_fee, fees.harbor_maintenance_fee, fees.subtotal)


def test_validators():
    desc, value = InputValidator.validate_calculation_input({"description": "leather handbag", "value": 500})
    assert desc == "leather handbag" and value == 500.0
    for bad in (
        {"description": "x", "value": 1},
        {"description": "leather handbag", "value": 0},
        {"description": "leather handbag", "value": "abc"},
    ):
        try:
            InputValidator.validate_calculation_input(bad)
            raise AssertionError("expected ValidationError for " + repr(bad))
        except ValidationError:
            pass
    print("validators OK")


def main():
    service = TariffDataService()
    service.load_data()
    records = service.records
    assert records, "no records loaded"
    print("loaded", len(records), "records")

    test_validators()
    test_fees()
    test_lexical_matching(records)
    test_llm_matching(records)
    test_llm_falls_back_when_non_committal(records)
    test_semantic_matching(records)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
