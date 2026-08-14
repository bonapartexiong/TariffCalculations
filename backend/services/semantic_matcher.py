"""
Semantic product matching: LLM embeddings first, lexical TF-IDF fallback.

The primary path embeds the user query with an LLM embedding model and
measures cosine similarity against precomputed embeddings of the tariff
corpus. This captures *meaning* (e.g. "sneakers" and "athletic footwear"),
not just shared words, which is what distinguishes it from the original
TF-IDF + cosine implementation.

A pure-Python TF-IDF fallback keeps the API fully functional (and free to
host) when no embedding provider or precomputed index is configured, or when
the embedding endpoint is unreachable. NumPy is used opportunistically for
fast cosine scoring when it is installed.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter
from typing import List, Optional, Sequence

from backend.config import SIMILARITY_THRESHOLD, SEMANTIC_THRESHOLD, LLM_THRESHOLD, logger
from backend.exceptions import ProductMatchError
from backend.models import ProductMatch
from backend.services.embedding import EmbeddingProvider
from backend.services.llm_client import LLMClient, LLMError

try:
    import numpy as _np
except Exception:  # numpy is optional; pure-Python scoring is the fallback
    _np = None

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_HTS_QUERY_RE = re.compile(r"[0-9.\s]+")
_NON_DIGIT_RE = re.compile(r"\D")

# Standard English stopwords (matches the original scikit-learn
# stop_words="english" behaviour). "other" is intentionally kept because it is
# a meaningful catch-all category in the Harmonized Tariff Schedule.
_STOPWORDS = frozenset({
    "a", "about", "above", "after", "again", "against", "all", "am", "an",
    "and", "any", "are", "aren't", "as", "at", "be", "because", "been",
    "before", "being", "below", "between", "both", "but", "by", "can",
    "can't", "cannot", "could", "couldn't", "did", "didn't", "do", "does",
    "doesn't", "doing", "don't", "down", "during", "each", "few", "for",
    "from", "further", "had", "hadn't", "has", "hasn't", "have", "haven't",
    "having", "he", "he'd", "he'll", "he's", "her", "here", "here's",
    "hers", "herself", "him", "himself", "his", "how", "how's", "i",
    "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it",
    "it's", "its", "itself", "let's", "me", "more", "most", "mustn't", "my",
    "myself", "no", "nor", "not", "of", "off", "on", "once", "only", "or",
    "ought", "our", "ours", "ourselves", "out", "over", "own", "same",
    "shan't", "she", "she'd", "she'll", "she's", "should", "shouldn't", "so",
    "some", "such", "than", "that", "that's", "the", "their", "theirs",
    "them", "themselves", "then", "there", "there's", "these", "they",
    "they'd", "they'll", "they're", "they've", "this", "those", "through",
    "to", "too", "under", "until", "up", "very", "was", "wasn't", "we",
    "we'd", "we'll", "we're", "we've", "were", "weren't", "what", "what's",
    "when", "when's", "where", "where's", "which", "while", "who", "who's",
    "whom", "why", "why's", "with", "won't", "would", "wouldn't", "you",
    "you'd", "you'll", "you're", "you've", "your", "yours", "yourself",
    "yourselves",
})


def _stem(word: str) -> str:
    """Light, dependency-free stemmer so 'handbag' matches 'handbags'."""
    if len(word) <= 3:
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("sses", "shes", "ches", "xes", "zes")):
        return word[:-2]
    if word.endswith("ing") and len(word) > 5:
        return word[:-3]
    if word.endswith("ed") and len(word) > 4:
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _tokenize(text: str) -> List[str]:
    return [
        _stem(token)
        for token in _TOKEN_RE.findall(text.lower())
        if token not in _STOPWORDS and _stem(token) not in _STOPWORDS
    ]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity between two dense vectors."""
    if _np is not None:
        a = _np.asarray(a, dtype=_np.float64)
        b = _np.asarray(b, dtype=_np.float64)
        denom = float(_np.linalg.norm(a) * _np.linalg.norm(b))
        return float(_np.dot(a, b) / denom) if denom else 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _fingerprint(records: Sequence[dict]) -> str:
    """Stable fingerprint of the corpus so stale embedding caches are detected."""
    digest = hashlib.md5()
    for record in records:
        digest.update(record["search_text"].encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


class _LexicalIndex:
    """Small, dependency-free TF-IDF + cosine index for the fallback path."""

    def __init__(self, texts: Sequence[str]):
        self.texts = list(texts)
        self._build()

    def _build(self) -> None:
        n = len(self.texts)
        tokenized: List[Counter] = []
        self.doc_freq: Counter = Counter()

        for text in self.texts:
            tf = Counter(_tokenize(text))
            tokenized.append(tf)
            for term in tf:
                self.doc_freq[term] += 1

        self.vectors: List[dict] = []
        for tf in tokenized:
            vector: dict = {}
            norm2 = 0.0
            for term, count in tf.items():
                idf = math.log((1 + n) / (1 + self.doc_freq[term])) + 1.0
                weight = count * idf
                vector[term] = weight
                norm2 += weight * weight
            norm = math.sqrt(norm2) or 1.0
            self.vectors.append({k: v / norm for k, v in vector.items()})

    def query_vector(self, text: str) -> dict:
        tf = Counter(_tokenize(text))
        if not tf:
            return {}
        n = len(self.texts)
        vector: dict = {}
        norm2 = 0.0
        for term, count in tf.items():
            idf = math.log((1 + n) / (1 + self.doc_freq.get(term, 0))) + 1.0
            weight = count * idf
            vector[term] = weight
            norm2 += weight * weight
        norm = math.sqrt(norm2) or 1.0
        return {k: v / norm for k, v in vector.items()}

    def score_all(self, query_vector: dict):
        """Return [(cosine, index), ...] sorted descending for every document."""
        scored = []
        for i, vector in enumerate(self.vectors):
            # Dot the query against the smaller of the two sparse dicts.
            if len(query_vector) <= len(vector):
                score = sum(v * vector.get(k, 0.0) for k, v in query_vector.items())
            else:
                score = sum(v * query_vector.get(k, 0.0) for k, v in vector.items())
            if score > 0:
                scored.append((score, i))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored

    def best_match(self, query_vector: dict):
        """Return (index, cosine) of the best matching document."""
        ranked = self.score_all(query_vector)
        if not ranked:
            return -1, 0.0
        return ranked[0][1], ranked[0][0]

    def top_matches(self, query_vector: dict, k: int):
        """Return the top-k (cosine, index) matches."""
        return self.score_all(query_vector)[:k]


class SemanticMatcher:
    """Matches a user description to a tariff record, LLM-first."""

    def __init__(
        self,
        records: Sequence[dict],
        provider: Optional[EmbeddingProvider] = None,
        cache_dir: Optional[str] = None,
        llm_client: Optional[LLMClient] = None,
        llm_candidates: int = 10,
    ):
        self.records = list(records)
        self.provider = provider
        self.cache_dir = cache_dir
        self.llm_client = llm_client
        self.llm_candidates = llm_candidates
        self.corpus_embeddings: Optional[Sequence[Sequence[float]]] = None
        self.embedding_model: Optional[str] = None
        self._lexical_index: Optional[_LexicalIndex] = None
        self._corpus_np = None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def find_match(self, description: str) -> ProductMatch:
        """Return the best tariff match for a user description."""
        if not self.records:
            raise ValueError("Tariff data not loaded")

        # 1) Fast, exact path when the user typed an HTS code.
        hts_match = self._match_hts_code(description)
        if hts_match is not None:
            return hts_match

        # 2) LLM semantic ranking (chat model such as DeepSeek).
        #    Best-effort: any non-committal reply or failure degrades gracefully.
        if self.llm_client is not None:
            try:
                return self._llm_match(description)
            except ProductMatchError as exc:
                logger.warning("LLM returned no confident match (%s); falling back", exc)
            except Exception as exc:
                logger.warning(
                    "LLM ranking failed (%s); falling back to lexical TF-IDF",
                    exc,
                )

        # 3) LLM semantic matching (embeddings).
        if self._semantic_ready():
            try:
                query_vector = self.provider.embed([description])[0]
                index, score = self._best_semantic_match(query_vector)
                if index >= 0:
                    confidence = max(0.0, min(1.0, score))
                    if confidence >= SEMANTIC_THRESHOLD:
                        return self._make_match(index, confidence, "llm_embeddings")
                    logger.warning(
                        "Embedding match below threshold (%.2f); falling back",
                        confidence,
                    )
            except Exception as exc:  # network/format failure -> degrade gracefully
                logger.warning(
                    "Semantic matching failed (%s); falling back to lexical TF-IDF",
                    exc,
                )

        # 4) Lexical TF-IDF fallback (works with no API key / no network).
        return self._lexical_match(description)

    def build_index(self, batch_size: int = 500) -> None:
        """Embed every tariff record and persist the embedding cache."""
        if self.provider is None:
            raise RuntimeError("No embedding provider configured")
        if not self.cache_dir:
            raise RuntimeError("No cache directory configured")

        texts = [record["search_text"] for record in self.records]
        vectors: List[List[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            vectors.extend(self.provider.embed(batch))
            logger.info("Embedded %d/%d records", len(vectors), len(texts))

        self._write_cache(vectors)
        self.corpus_embeddings = vectors
        self.embedding_model = self.provider.model
        self._prepare_corpus()

    # ------------------------------------------------------------------ #
    # Matching internals
    # ------------------------------------------------------------------ #
    def _make_match(self, index: int, confidence: float, source: str) -> ProductMatch:
        record = self.records[index]
        return ProductMatch(
            description=record["description"],
            group=record.get("group", ""),
            tariff_rate=float(record["tariff"]),
            confidence=confidence,
            index=index,
            hts_number=record.get("hts_number", ""),
            match_source=source,
        )

    def _match_hts_code(self, description: str) -> Optional[ProductMatch]:
        """Match an explicit HTS code (e.g. 4202.11.00) exactly or by prefix."""
        text = description.strip()
        if not text or not _HTS_QUERY_RE.fullmatch(text):
            return None
        digits = _NON_DIGIT_RE.sub("", text)
        if not digits:
            return None

        exact, best_prefix, best_prefix_len = -1, -1, -1
        for i, record in enumerate(self.records):
            hts_digits = _NON_DIGIT_RE.sub("", record.get("hts_number", ""))
            if not hts_digits:
                continue
            if hts_digits == digits:
                exact = i
                break
            if hts_digits.startswith(digits):
                # Prefer the shortest (broadest) heading that matches the code.
                if best_prefix == -1 or len(hts_digits) < best_prefix_len:
                    best_prefix, best_prefix_len = i, len(hts_digits)

        if exact >= 0:
            return self._make_match(exact, 1.0, "hts_code")
        if best_prefix >= 0 and len(digits) >= 6:
            return self._make_match(best_prefix, 0.9, "hts_code")
        return None

    def _candidate_records(self, description: str, k: int):
        """Return top-k lexical candidates as [(record_index, record), ...]."""
        if self._lexical_index is None:
            self._lexical_index = _LexicalIndex(
                [record["search_text"] for record in self.records]
            )
        query_vector = self._lexical_index.query_vector(description)
        if not query_vector:
            return []
        ranked = self._lexical_index.top_matches(query_vector, k)
        return [(idx, self.records[idx]) for _score, idx in ranked]

    def _llm_match(self, description: str) -> ProductMatch:
        """Use a chat LLM to pick the best candidate from lexical top-k."""
        candidates = self._candidate_records(description, self.llm_candidates)
        if not candidates:
            return self._lexical_match(description)

        lines = []
        for position, (_record_index, record) in enumerate(candidates):
            hts = record.get("hts_number") or "-"
            lines.append(
                f"{position}. HTS {hts} | {record['description']} | {record.get('group', '')}"
            )

        system = (
            "You are a US customs tariff classification expert. Given a product "
            "description and a numbered list of candidate HTS lines, choose the "
            "single best match. Respond with ONLY a JSON object in the form "
            '{"index": <int>, "confidence": <number from 0 to 1>, "reason": "<short>"}. '
            "Use index -1 if none of the candidates match well."
        )
        user = f"Product: {description}\n\nCandidates:\n" + "\n".join(lines)

        data = self.llm_client.complete_json(system, user)

        try:
            position = int(data.get("index", -1))
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError) as exc:
            raise LLMError(f"invalid LLM ranking payload: {data!r}") from exc

        if position < 0 or position >= len(candidates):
            raise ProductMatchError(
                "The LLM could not confidently classify this product. "
                "Please provide a more specific description."
            )

        if confidence < LLM_THRESHOLD:
            raise ProductMatchError(
                f"Low confidence LLM match (confidence: {confidence:.2f}). "
                "Please provide a more specific product description."
            )

        record_index = candidates[position][0]
        return self._make_match(record_index, confidence, "llm")

    def _semantic_ready(self) -> bool:
        if self.provider is None:
            return False
        if self.corpus_embeddings is None:
            self._load_cache()
        if self.corpus_embeddings is None:
            logger.warning(
                "Embedding provider configured but no corpus embedding cache found; "
                "run scripts/build_embeddings.py (or set EMBEDDING_LAZY_BUILD=true) - "
                "using lexical fallback",
            )
            return False
        return True

    def _best_semantic_match(self, query_vector):
        """Return (index, cosine) of the best semantic match."""
        if _np is not None and self._corpus_np is not None:
            q = _np.asarray(query_vector, dtype=_np.float64)
            q_norm = float(_np.linalg.norm(q))
            if q_norm == 0:
                return -1, 0.0
            q = q / q_norm
            norms = _np.linalg.norm(self._corpus_np, axis=1)
            sims = (self._corpus_np @ q) / (norms + 1e-12)
            best = int(_np.argmax(sims))
            return best, float(sims[best])

        best_idx, best_score = -1, -1.0
        for i, vector in enumerate(self.corpus_embeddings):
            score = _cosine(query_vector, vector)
            if score > best_score:
                best_score, best_idx = score, i
        return best_idx, best_score

    def _lexical_match(self, description: str) -> ProductMatch:
        if self._lexical_index is None:
            self._lexical_index = _LexicalIndex(
                [record["search_text"] for record in self.records]
            )
        index, score = self._lexical_index.best_match(
            self._lexical_index.query_vector(description)
        )
        if index < 0 or score < SIMILARITY_THRESHOLD:
            raise ProductMatchError(
                f"Low confidence match (confidence: {score:.2f}). "
                "Please provide a more specific product description.",
            )
        return self._make_match(index, score, "lexical_tfidf")

    # ------------------------------------------------------------------ #
    # Embedding cache persistence
    # ------------------------------------------------------------------ #
    def _prepare_corpus(self) -> None:
        if _np is not None and self.corpus_embeddings is not None:
            try:
                self._corpus_np = _np.asarray(
                    self.corpus_embeddings, dtype=_np.float64
                )
            except Exception:
                self._corpus_np = None

    def _load_cache(self) -> None:
        if not self.cache_dir:
            return
        meta_path = os.path.join(self.cache_dir, "embeddings_meta.json")
        npz_path = os.path.join(self.cache_dir, "embeddings.npz")
        json_path = os.path.join(self.cache_dir, "embeddings.json")

        meta = {}
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as fh:
                    meta = json.load(fh)
            except Exception as exc:
                logger.warning("Could not read embedding metadata: %s", exc)

        # Stale-cache guard: the corpus fingerprint must match.
        if meta and meta.get("corpus_hash") != _fingerprint(self.records):
            logger.warning(
                "Embedding cache is stale (tariff data changed); rebuild with "
                "scripts/build_embeddings.py",
            )
            return

        vectors = None
        if os.path.exists(npz_path) and _np is not None:
            try:
                data = _np.load(npz_path)
                vectors = data["embeddings"].tolist()
            except Exception as exc:
                logger.warning("Could not load embeddings.npz: %s", exc)
        elif os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as fh:
                    vectors = json.load(fh).get("embeddings")
            except Exception as exc:
                logger.warning("Could not load embeddings.json: %s", exc)

        if vectors is not None and len(vectors) == len(self.records):
            self.corpus_embeddings = vectors
            self.embedding_model = meta.get("model") or self.provider.model
            self._prepare_corpus()
            logger.info("Loaded %d corpus embeddings", len(vectors))
        elif vectors is not None:
            logger.warning(
                "Embedding cache has %d vectors but corpus has %d records; ignoring cache",
                len(vectors),
                len(self.records),
            )

    def _write_cache(self, vectors: Sequence[Sequence[float]]) -> None:
        meta = {
            "model": self.provider.model,
            "dimensions": len(vectors[0]) if vectors else 0,
            "count": len(vectors),
            "corpus_hash": _fingerprint(self.records),
        }
        meta_path = os.path.join(self.cache_dir, "embeddings_meta.json")
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2)

        if _np is not None:
            npz_path = os.path.join(self.cache_dir, "embeddings.npz")
            _np.savez_compressed(
                npz_path, embeddings=_np.asarray(vectors, dtype=_np.float32)
            )

        json_path = os.path.join(self.cache_dir, "embeddings.json")
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump({"embeddings": vectors}, fh)

        logger.info("Wrote embedding cache (model=%s, count=%d)", meta["model"], meta["count"])
