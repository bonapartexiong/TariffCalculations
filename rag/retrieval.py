"""Hybrid retrieval over HTS chunks (Module B).

BM25 (k1=1.5, b=0.8) over chunk text + description + heading context,
with exact-HTS lookup, query-expansion synonyms (expansion tokens only —
ranking is still BM25), and optional embeddings when sentence-transformers
is available (otherwise BM25 only, reported in ``Index.engine``).

Conforms to CONTRACTS.md:
  Index.build(chunks) / Index.load(path) / Index.save(path)
  Index.search(query, top_k=10) -> list[RetrievalHit]
  Index.lookup_hts(code) -> (chunk|None, warning|None)
  RetrievalHit = {"chunk", "score" in [0,1], "match_type", "warning"}
"""
import json
import math
import re
from collections import Counter

# ---------------------------------------------------------------- tokens

STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "with",
    "by", "is", "are", "was", "were", "be", "been", "what", "how", "which",
    "whats", "what's", "do", "does", "did", "i", "me", "my", "we", "you",
    "your", "he", "she", "it", "its", "they", "them", "their", "this",
    "that", "these", "those", "there", "here", "from", "at", "as", "an",
    "per", "under", "over", "into", "about", "between", "through", "during",
    "before", "after", "above", "below", "up", "down", "out", "off", "again",
    "further", "then", "once", "all", "any", "both", "each", "few", "more",
    "most", "same", "so", "than", "too", "very", "can", "will", "just",
    "should", "now", "get", "find", "show", "give", "tell", "please",
    "duty", "rate", "rates", "classification", "classify", "classified",
    "import", "imported", "product", "item", "items", "thing", "things",
}
# NOTE: "no" is deliberately NOT a stopword; "no other" is kept as the
# bigram token "no_other" so it never collapses to "other".

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[0-9]+)+|[a-z0-9]+")


def _stem(tok):
    if "." in tok:  # HTS codes / decimals: never stem
        return tok
    if tok.endswith("ies") and len(tok) > 4:
        return tok[:-3] + "y"
    if tok.endswith("es") and len(tok) > 4:
        return tok[:-2]
    if tok.endswith("s") and len(tok) > 3 and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def tokenize(text):
    """Lowercase, strip punctuation except HTS dots, light stemming.

    Keeps the bigram ``no_other`` for the phrase "no other".
    """
    if not text:
        return []
    raw = _TOKEN_RE.findall(str(text).lower())
    out = []
    i = 0
    while i < len(raw):
        if raw[i] == "no" and i + 1 < len(raw) and raw[i + 1] == "other":
            out.append("no_other")
            i += 2
            continue
        tok = raw[i]
        if tok in STOPWORDS:
            i += 1
            continue
        out.append(_stem(tok))
        i += 1
    return out


# ------------------------------------------------------- synonym expansion
# Query-expansion tokens ONLY. These never hard-code an answer: the
# expanded tokens are added to the BM25 query and ranking is still BM25.
SYNONYM_EXPANSIONS = {
    "sneakers": "athletic footwear",
    "sneaker": "athletic footwear",
    "trainers": "athletic footwear",
    "laptop": "automatic data processing portable",
    "laptops": "automatic data processing portable",
    "notebook computer": "automatic data processing portable",
    "wine": "wine 2204",
    "pharmaceuticals": "medicaments",
    "pharmaceutical": "medicaments",
    "medicine": "medicaments",
    "medicines": "medicaments",
    "drugs": "medicaments",
    "car parts": "8708",
    "car part": "8708",
    "auto parts": "8708",
    "automobile parts": "8708",
    "vehicle parts": "8708",
    "steel screws": "7318.15",
    "screws": "7318",
    "plastic toys": "9503",
    "toys": "9503",
    "coffee beans": "0901.11 0901.21",
    "coffee": "0901",
}


def expand_query_tokens(query):
    """Base tokens + synonym-expansion tokens for the query."""
    base = tokenize(query)
    lowered = " " + str(query).lower() + " "
    expanded = list(base)
    # Longest phrases first so "coffee beans" wins over "coffee".
    for phrase in sorted(SYNONYM_EXPANSIONS, key=len, reverse=True):
        if phrase in lowered or phrase in base:
            expanded.extend(tokenize(SYNONYM_EXPANSIONS[phrase]))
    return expanded


# ------------------------------------------------------------ HTS normalize
_HTS_DOTTED = re.compile(r"^\d{4}(?:\.\d{2}){0,3}$")


def normalize_hTS(code):
    """Normalize an HTS code to canonical dotted form, max 8 digits.

    Accepts "4202.11", "4202.11.00", "4202.11.00.10" (10-digit truncated
    to the 8-digit dutiable line), and "4202110010" (dots inserted).
    """
    if code is None:
        return ""
    s = str(code).strip().replace(" ", "").replace("-", "").rstrip(".")
    if not s:
        return ""
    if re.fullmatch(r"\d+", s):
        if len(s) == 10:
            return "%s.%s.%s" % (s[:4], s[4:6], s[6:8])
        if len(s) == 8:
            return "%s.%s.%s" % (s[:4], s[4:6], s[6:8])
        if len(s) == 6:
            return "%s.%s" % (s[:4], s[4:6])
        if len(s) == 4:
            return s
        return s  # unknown length: return as-is, lookup will miss safely
    parts = [p for p in s.split(".") if p != ""]
    # Truncate beyond 8 digits (beyond the first 3 groups).
    parts = parts[:3]
    return ".".join(parts)


# Backwards-friendly alias.
normalize_hts = normalize_hTS


def _strip_trailing_00_candidates(norm):
    """norm plus progressively stripped trailing '.00' groups (heading forms)."""
    out = [norm]
    cur = norm
    while cur.endswith(".00"):
        cur = cur.rsplit(".", 1)[0]
        out.append(cur)
    return out


# Generic anti-false-positive terms: a chunk whose description is about
# these is demoted unless the query itself asks for them.
_DEMOTE_TERMS = ("waste", "lees", "residues", "residue", "slag", "ash", "scrap")

_HTS_PATTERN = re.compile(r"\d{4}(?:\.\d{2}){1,3}|\b\d{8,10}\b")


def detect_nonsense(query, hits, threshold=0.10):
    """True when there is no usable retrieval (max score < threshold)."""
    if not hits:
        return True
    return max(h["score"] for h in hits) < threshold


# Backwards-friendly alias.
is_nonsense_query = detect_nonsense
is_low_score = detect_nonsense


class Index:
    """BM25 (+ optional embeddings) index over chunk dicts."""

    K1 = 1.5
    B = 0.8
    LOW_SCORE_THRESHOLD = 0.10

    def __init__(self, chunks=None):
        self.chunks = []
        self.engine = "bm25"
        self._doc_tokens = []
        self._doc_counts = []
        self._doc_lens = []
        self._df = Counter()
        self._avgdl = 0.0
        self._embeddings = None
        self._model = None
        if chunks:
            self.build(chunks)

    # ------------------------------------------------------------ building
    def build(self, chunks):
        self.chunks = list(chunks or [])
        self._doc_tokens = [self._chunk_tokens(c) for c in self.chunks]
        self._doc_counts = [Counter(t) for t in self._doc_tokens]
        self._doc_lens = [len(t) for t in self._doc_tokens]
        self._df = Counter()
        for toks in self._doc_tokens:
            for t in set(toks):
                self._df[t] += 1
        self._avgdl = (
            sum(self._doc_lens) / len(self._doc_lens) if self._doc_lens else 0.0
        )
        self._embeddings = None
        self._model = None
        self._try_embeddings()
        return self

    def _chunk_tokens(self, chunk):
        parts = [chunk.get("text") or "", chunk.get("description") or ""]
        if chunk.get("hts"):
            parts.append(str(chunk["hts"]))
        return tokenize(" ".join(parts))

    def _try_embeddings(self):
        """Best-effort optional embeddings; stays BM25-only on any failure."""
        try:
            from sentence_transformers import SentenceTransformer
        except Exception:
            self.engine = "bm25"
            return
        try:
            import numpy as np  # noqa: F401  (required by the model)
            model = SentenceTransformer("all-MiniLM-L6-v2")
            texts = [
                " ".join(
                    [str(c.get("text") or ""), str(c.get("description") or "")]
                )
                for c in self.chunks
            ]
            if not texts:
                return
            emb = model.encode(texts, normalize_embeddings=True)
            self._model = model
            self._embeddings = emb
            self.engine = "bm25+embedding"
        except Exception:
            self._model = None
            self._embeddings = None
            self.engine = "bm25"

    # -------------------------------------------------------------- persist
    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            for chunk in self.chunks:
                f.write(json.dumps(chunk) + "\n")
        return path

    def load(self, path):
        chunks = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    chunks.append(json.loads(line))
        return self.build(chunks)

    # ------------------------------------------------------------ HTS lookup
    def lookup_hts(self, code):
        """Return (chunk|None, warning|None).

        Never returns an empty-HTS chunk. A heading/suffix (non-dutiable)
        row is returned only with a warning, never as a classification.
        """
        if code is None or str(code).strip() == "":
            return None, "No HTS code provided."
        norm = normalize_hTS(code)
        if not norm:
            return None, "Could not parse HTS code %r." % (code,)
        # Exact match (including stripped trailing-.00 heading fallbacks).
        for candidate in _strip_trailing_00_candidates(norm):
            exact = [
                c
                for c in self.chunks
                if c.get("hts") and normalize_hTS(c["hts"]) == candidate
            ]
            if exact:
                # Prefer a dutiable line; heading/suffix only with warning.
                for c in exact:
                    if c.get("dutiable"):
                        return c, None
                c = exact[0]
                return (
                    c,
                    "Only a heading/suffix row exists for %s (%s) — not a "
                    "dutiable classification line; see the dutiable lines "
                    "under this heading." % (code, candidate),
                )
        return None, "No line found for HTS code %s." % (code,)

    def _prefix_matches(self, norm):
        out = []
        for c in self.chunks:
            hts = c.get("hts")
            if not hts:
                continue
            nh = normalize_hTS(hts)
            if nh.startswith(norm + "."):
                out.append(c)
        # Dutiable lines first, then by HTS.
        out.sort(key=lambda c: (not c.get("dutiable"), str(c.get("hts") or "")))
        return out

    # --------------------------------------------------------------- scoring
    def _bm25_raw(self, query_tokens):
        n = len(self.chunks)
        scores = [0.0] * n
        if n == 0 or not query_tokens:
            return scores
        qf = Counter(query_tokens)
        avgdl = self._avgdl or 1.0
        for term in qf:
            df = self._df.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for i, counts in enumerate(self._doc_counts):
                tf = counts.get(term, 0)
                if tf == 0:
                    continue
                denom = tf + self.K1 * (
                    1 - self.B + self.B * (self._doc_lens[i] / avgdl)
                )
                scores[i] += idf * tf * (self.K1 + 1) / denom
        return scores

    def _boost(self, chunk, query_tokens):
        """Additive boosts/penalties applied after BM25 normalisation."""
        boost = 0.0
        if chunk.get("dutiable"):
            boost += 0.15  # dutiable lines over headings
        kind = chunk.get("kind") or ""
        if kind in ("tariff_line", "chapter99"):
            boost += 0.05  # tariff lines over notes for description queries
        elif kind in (
            "general_note", "chapter_note", "section_note",
            "gri", "statistical_note", "appendix",
        ):
            boost -= 0.05
        desc = (chunk.get("description") or "").strip().lower()
        # "Other" rows deprioritised vs specific rows.
        if (
            desc == "other"
            or desc.startswith("other ")
            or desc.startswith("other;")
            or desc.startswith("other,")
            or desc.startswith("other.")
            or desc == "other:"
        ):
            boost -= 0.12
        # Waste / lees / residue lines demoted unless the query asks for them.
        hay = ((chunk.get("description") or "") + " " + (chunk.get("text") or "")).lower()
        qtok = set(query_tokens)
        for term in _DEMOTE_TERMS:
            if term in hay and term not in qtok and _stem(term) not in qtok:
                boost -= 0.15
                break
        return boost

    def _embedding_scores(self, query):
        if self._embeddings is None or self._model is None:
            return None
        try:
            import numpy as np

            q = self._model.encode([query], normalize_embeddings=True)[0]
            sims = (self._embeddings @ q).tolist()
            return [max(0.0, min(1.0, float(s))) for s in sims]
        except Exception:
            return None

    # ---------------------------------------------------------------- search
    def search(self, query, top_k=10):
        if not query or not str(query).strip():
            return []
        query = str(query)

        # 1) Exact HTS first when the query contains an HTS pattern.
        m = _HTS_PATTERN.search(query.replace(" ", ""))
        if m:
            norm = normalize_hTS(m.group(0))
            chunk, warning = self.lookup_hts(m.group(0))
            if chunk is not None:
                return [{
                    "chunk": chunk,
                    "score": 1.0,
                    "match_type": "exact_hts",
                    "warning": warning,
                }]
            prefix = self._prefix_matches(norm)
            if prefix:
                hits = []
                for c in prefix[:top_k]:
                    hits.append({
                        "chunk": c,
                        "score": 0.85,
                        "match_type": "prefix_hts",
                        "warning": (
                            "Prefix match under %s — confirm the full "
                            "8-digit dutiable line." % norm
                        ),
                    })
                return hits
            # No exact/prefix HTS hit: fall through to BM25.

        # 2) BM25 (+ optional embeddings) over expanded query tokens.
        query_tokens = expand_query_tokens(query)
        raw = self._bm25_raw(query_tokens)
        emb = self._embedding_scores(query)
        scored = []
        for i, chunk in enumerate(self.chunks):
            bm25_norm = raw[i] / (raw[i] + 1.5) if raw[i] > 0 else 0.0
            if emb is not None:
                base = 0.5 * bm25_norm + 0.5 * emb[i]
                match_type = "embedding"
            else:
                base = bm25_norm
                match_type = "bm25"
            # Boosts must never create a score from zero evidence:
            # a chunk with no BM25/embedding match is not a hit.
            if base <= 0.0:
                continue
            score = base + self._boost(chunk, query_tokens)
            score = max(0.0, min(1.0, score))
            if score <= 0.0:
                continue
            warning = None
            if chunk.get("kind") == "tariff_line" and not chunk.get("dutiable"):
                warning = (
                    "Non-dutiable heading/suffix row — not a classification; "
                    "see the dutiable lines under this heading."
                )
            scored.append({
                "chunk": chunk,
                "score": float(score),
                "match_type": match_type,
                "warning": warning,
            })
        scored.sort(key=lambda h: (-h["score"], str(h["chunk"].get("hts") or "")))

        # Dedupe by hts+page (notes with no HTS dedupe by id).
        seen = set()
        deduped = []
        for h in scored:
            c = h["chunk"]
            key = (
                (c.get("hts"), c.get("page"))
                if c.get("hts")
                else ("__id__", c.get("id"))
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(h)

        hits = deduped[:top_k]
        # Nonsense / low-score queries -> empty.
        if detect_nonsense(query, hits, self.LOW_SCORE_THRESHOLD):
            return []
        return hits
