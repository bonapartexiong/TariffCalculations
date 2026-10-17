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
    # HTS vocabulary, not layman vocabulary: the HTS says "Sports
    # footwear; tennis shoes, basketball shoes, gym shoes, training
    # shoes", never "sneakers" or "athletic footwear". Expanding to the
    # HTS words is what makes the match; "athletic" alone matches
    # nothing in the HTS and is kept only as a harmless extra token.
    "sneakers": "athletic sports footwear tennis shoes basketball gym training",
    "sneaker": "athletic sports footwear tennis shoes basketball gym training",
    "trainers": "athletic sports footwear tennis shoes basketball gym training",
    "athletic footwear": "sports footwear tennis shoes basketball gym training",
    "athletic": "sports",
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
    """Base tokens + synonym-expansion tokens for the query.

    Two modes:

    * ADDITIVE (default): expansion tokens are added to the base
      tokens. Used for HTS-code expansions ("coffee beans" ->
      "0901.11 0901.21") and multi-word HTS-vocabulary expansions
      ("sneakers" -> sports/footwear/tennis/...), where the base
      tokens are still useful HTS words.

    * REPLACEMENT (exact single-word layman synonym only): when the
      whole query IS one layman word whose expansion is a single HTS
      word, the layman token is REPLACED, not added. "pharmaceuticals"
      alone means Ch. 30 medicaments — but the HTS also uses
      "pharmaceutical" for the narrower 3006 goods (placebos, waste),
      so keeping the base token lets 3006 outrank 3004 on
      pharmaceutical TF. Replacing it with "medicament" fixes that.
      Replacement applies ONLY on an exact whole-query match, so
      "pharmaceutical waste" (a real HTS concept) keeps its base
      tokens and still finds the waste line.
    """
    base = tokenize(query)
    lowered = " " + str(query).lower() + " "
    stripped = str(query).strip().lower()
    expanded = list(base)
    # Longest phrases first so "coffee beans" wins over "coffee".
    for phrase in sorted(SYNONYM_EXPANSIONS, key=len, reverse=True):
        if phrase in lowered or phrase in base:
            expansion = SYNONYM_EXPANSIONS[phrase]
            exp_tokens = tokenize(expansion)
            # Exact single-word layman synonym -> replacement.
            if (
                stripped == phrase
                and " " not in phrase
                and " " not in expansion
                and not any(ch.isdigit() for ch in expansion)
            ):
                for tok in tokenize(phrase):
                    if tok in expanded:
                        expanded.remove(tok)
                expanded.extend(exp_tokens)
            else:
                expanded.extend(exp_tokens)
    # Dedupe, preserving order: "pharmaceuticals" matches both the
    # "pharmaceuticals" and "pharmaceutical" phrases (substring), and
    # must not emit "medicament" twice.
    return list(dict.fromkeys(expanded))


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

# Stemmed generic HTS phrasing, excluded from the specificity penalty:
# value / quantity / unit / gender / "other" / boilerplate ("put up in
# measured doses ... for retail sale") — these are not product
# qualifiers. Stemmed forms match tokenize() output ("doses"->"dos",
# "forms"->"form", "packings"->"packing", "litres"->"litr").
_GENERIC_LEAF_TOKENS = frozenset({
    "valued", "each", "pair", "kg", "liter", "litr", "doz", "dozen",
    "gross", "head", "jewel", "article", "number", "other",
    "men", "women", "boy", "girl", "infant", "baby", "child", "adult",
    "unisex", "containing", "without", "not", "put", "measured", "dos",
    "dose", "form", "packing", "retail", "sale", "container", "weighing",
    "less", "whether", "mm", "cm", "mg", "g", "m",
})

_HTS_PATTERN = re.compile(r"\d{4}(?:\.\d{2}){1,3}|\b\d{8,10}\b")


def _hts_prefix_tokens(hts):
    """HTS + every prefix level: "4202.21.90" -> 4202, 4202.21, 4202.21.90.

    Prefix tokens make synonym expansions that name an HTS (e.g.
    "coffee beans" -> "0901.11") match the leaves under it, and let
    heading-level queries match. Without them, "2204" never equals
    the single token "2204.21.00" and the expansion is dead weight.
    """
    if not hts:
        return []
    parts = str(hts).strip().split(".")
    out = []
    for i in range(1, len(parts) + 1):
        out.append(".".join(parts[:i]))
    return out


def detect_nonsense(query, hits, threshold=0.10):
    """True when there is no usable retrieval (max score < threshold).

    NOTE: ``search()`` no longer feeds max-normalised display scores
    here — the top hit is always 1.0 after max-normalisation, which
    would make nonsense undetectable. ``search()`` applies its raw
    threshold before normalising. This helper is kept for backwards
    compatibility and operates on whatever scores it is given.
    """
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
    # RAW BM25 threshold (pre-normalisation): a query whose best
    # rank-score is below this has no real evidence. The old value
    # 0.10 was a *normalised* threshold (raw/(raw+1.5)); 0.5 raw is the
    # closest equivalent that still rejects weak single-token noise
    # while accepting every real description query (typical raw: 3-15).
    LOW_SCORE_THRESHOLD = 0.5

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
        """Index tokens for one chunk — rate boilerplate EXCLUDED.

        Tariff lines: HTS (+ prefix tokens) + leaf description +
        full_description (ancestor chain). The chunk ``text`` field is
        deliberately NOT indexed for tariff lines: it embeds the rate
        cells ("General: 9% | Special: Free (A, AU, ...) | Col2: 35%"),
        whose tokens ("free", "general", "35", program symbols) would
        otherwise match unrelated queries and drown real description
        evidence — e.g. every Free line matching a query for "free".

        Non-tariff chunks (notes / GRI / appendix) have no rate cells,
        so their ``text`` IS the content and is indexed. Tariff chunks
        from an old index / synthetic fixture that carry no
        ``full_description`` key fall back to ``text`` (synthetic texts
        are clean description text, not rate cells).
        """
        kind = chunk.get("kind") or ""
        parts: list[str] = []
        if chunk.get("hts"):
            parts.append(str(chunk["hts"]))
            parts.extend(_hts_prefix_tokens(chunk["hts"]))
        if chunk.get("description"):
            parts.append(str(chunk["description"]))
        if "full_description" in chunk:
            if chunk.get("full_description"):
                parts.append(str(chunk["full_description"]))
            # Key present (new index): never fall back to rate-laden text,
            # even when full_description is empty ("Other" leaves etc.).
        elif kind in ("tariff_line", "chapter99"):
            parts.append(str(chunk.get("text") or ""))
        else:
            parts.append(str(chunk.get("text") or ""))
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
        """Additive boosts/penalties in RAW BM25 units (see search()).

        Magnitudes are raw-scale (typical raw 3-30): the old ±0.15
        boosts were rounding error next to raw gaps of 2-4 and could
        never flip a heading over a leaf or a waste line below a
        medicaments line.
        """
        boost = 0.0
        kind = chunk.get("kind") or ""
        # Only an EXPLICIT dutiable=False is a heading/suffix row.
        # Chunks that lack the key entirely (synthetic fixtures, old
        # indexes) have unknown dutiability and must stay neutral —
        # penalising them would drop real matches (e.g. the GSP test
        # fixture's tariff line) for a missing key.
        if chunk.get("dutiable") is True:
            boost += 1.0  # dutiable lines over headings/suffix rows
        elif chunk.get("dutiable") is False and kind == "tariff_line":
            boost -= 1.0  # heading / suffix rows are not classifications
        elif kind == "chapter99":
            boost += 0.05  # additional-duty lines stay findable for §301 etc.
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
            boost -= 0.5
        # Waste / lees / residue lines demoted unless the query asks for them.
        # Hay is description + full_description, NOT the rate-laden text.
        hay_parts = [chunk.get("description") or ""]
        if "full_description" in chunk:
            hay_parts.append(chunk.get("full_description") or "")
        else:
            hay_parts.append(chunk.get("text") or "")
        hay = " ".join(hay_parts).lower()
        qtok = set(query_tokens)
        for term in _DEMOTE_TERMS:
            if term in hay and term not in qtok and _stem(term) not in qtok:
                boost -= 1.0
                break
        # Exclusion penalty: a query term that appears ONLY inside an
        # exclusion ("other than medicaments", "excluding ...") is the
        # OPPOSITE of a match — 3304's heading mentions medicaments
        # only to exclude them, so "pharmaceuticals" must not rank
        # 3304 (manicure preparations) above Ch. 30, and 3006.93.80's
        # leaf ("...containing other chemicals other than
        # medicaments") is placebos, explicitly NOT medicaments.
        # Penalise only when EVERY occurrence is exclusion-context; a
        # term that also appears outside the exclusion (3004's
        # "Medicaments (excluding goods of heading 3002...)") is not
        # penalised. Checked on RAW words (not stemmed tokens): "than"
        # is a stopword, so tokenization would collapse "other than
        # X" to "other X" — indistinguishable from the "Other: X"
        # leaf header ("Other footwear"), which is NOT an exclusion.
        # The raw check requires the exact sequence "other than X"
        # for the "other" case. Leaf and ancestors are checked
        # SEPARATELY so the leaf's own "Other" cannot bleed into the
        # following heading text.
        if "full_description" in chunk:
            full = chunk.get("full_description") or ""
            leaf = chunk.get("description") or ""
            if full.endswith(leaf):
                ancestors = full[: len(full) - len(leaf)].rstrip(" |")
            else:
                parts = full.split(" | ")
                ancestors = " | ".join(parts[:-1]) if len(parts) > 1 else ""
            word_lists = [
                re.findall(r"[a-z0-9]+", leaf.lower()),
                re.findall(r"[a-z0-9]+", ancestors.lower()),
            ]
        else:
            word_lists = [re.findall(r"[a-z0-9]+", hay.lower())]
        for qt in qtok:
            if "." in qt:
                continue
            all_positions = []
            for words in word_lists:
                all_positions.extend(
                    (words, i)
                    for i, w in enumerate(words)
                    if _stem(w) == qt
                )
            if not all_positions:
                continue
            def _is_exclusion(words, i):
                window = words[max(0, i - 2):i]
                if any(
                    m in window
                    for m in ("excluding", "except", "exclud", "not")
                ):
                    return True
                return i >= 2 and words[i - 2] == "other" and words[i - 1] == "than"
            if all(_is_exclusion(words, i) for words, i in all_positions):
                boost -= 3.0
                break
        boost += self._specificity_penalty(chunk, qtok)
        return boost

    def _coverage_bonus(self, counts, query_tokens):
        """Reward matching MORE distinct query terms (raw BM25 units).

        BM25 length-normalisation lets a very short doc matching one
        rare term (3926.90.33 "Handbags" matching only "handbag")
        beat a long doc matching both query terms (4202.21.90 matching
        "leather" + "handbag"). A generic query should prefer the doc
        that covers the whole query: +1.0 per distinct matched term
        beyond the first, capped at +5.0.
        """
        matched = sum(1 for t in set(query_tokens) if counts.get(t, 0) > 0)
        return 1.0 * min(max(matched - 1, 0), 5)

    def _specificity_penalty(self, chunk, query_tokens):
        """Penalise a leaf that adds specific qualifiers the query lacks.

        A generic query ("leather handbag") should hit the generic leaf
        (4202.21.90 "Valued over $20 each"), not a sibling whose leaf
        adds an unasked-for qualifier ("Of reptile leather"). Both
        share the same ancestors, so BM25 alone ranks the reptile row
        higher on extra "leather" TF. The penalty counts leaf-only
        tokens (in the leaf description but not in the ancestors,
        not in the query, not generic HTS phrasing like "valued",
        numbers, units, gender, "other") at -1.5 each, capped at -3.0.
        A query that DOES name the qualifier ("reptile leather
        handbag") pays no penalty, so specific queries still hit
        specific leaves. Skipped when the chunk has no
        ``full_description`` (synthetic / old-index chunks), where
        ancestors are unknown.
        """
        if "full_description" not in chunk:
            return 0.0
        full = chunk.get("full_description") or ""
        leaf = chunk.get("description") or ""
        if not full or not leaf:
            return 0.0
        if full.endswith(leaf):
            ancestors = full[: len(full) - len(leaf)].rstrip(" |")
        else:
            parts = full.split(" | ")
            ancestors = " | ".join(parts[:-1]) if len(parts) > 1 else ""
        leaf_toks = set(tokenize(leaf))
        anc_toks = set(tokenize(ancestors))
        unmatched = [
            t for t in leaf_toks
            if t not in anc_toks
            and t not in query_tokens
            and t not in _GENERIC_LEAF_TOKENS
            and not t.isdigit()
            and "." not in t
        ]
        return -1.5 * min(len(unmatched), 2)

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
    def search(self, query, top_k=10, dutiable_only=False):
        """BM25 search. ``dutiable_only=True`` restricts the BM25 pool
        to dutiable tariff lines BEFORE top_k is applied, so the
        caller (calculate / candidates) gets dutiable lines even when
        headings/suffix rows would otherwise fill the whole top_k
        window. The HTS-code path is unaffected (lookup_hts already
        prefers a dutiable line)."""
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
        # RANKING USES UNCLAMPED SCORES. The old code normalised each
        # chunk independently (raw/(raw+1.5)) + boost, which clamped
        # distinct raws (11.68 vs 8.92) to the same 1.0 and let the
        # HTS tie-break pick the wrong line (shoe polish over
        # handbags). Now: rank by raw BM25 (+ embedding + boost), then
        # max-normalise ONCE for display scores in [0,1] — AFTER
        # ranking, never before.
        query_tokens = expand_query_tokens(query)
        raw = self._bm25_raw(query_tokens)
        emb = self._embedding_scores(query)
        ranked = []  # (rank_score, chunk, match_type)
        for i, chunk in enumerate(self.chunks):
            if dutiable_only and not (
                chunk.get("kind") == "tariff_line" and chunk.get("dutiable")
            ):
                continue
            has_evidence = raw[i] > 0.0 or (
                emb is not None and emb[i] > 0.0
            )
            # Boosts must never create a score from zero evidence.
            if not has_evidence:
                continue
            if emb is not None:
                # Embedding cosine lives in [0,1]; scale it into raw
                # BM25 units (typical raw 3-15) so it can break ties
                # without drowning BM25.
                rank = (
                    raw[i]
                    + 2.0 * emb[i]
                    + self._boost(chunk, query_tokens)
                    + self._coverage_bonus(self._doc_counts[i], query_tokens)
                )
                match_type = "embedding"
            else:
                rank = (
                    raw[i]
                    + self._boost(chunk, query_tokens)
                    + self._coverage_bonus(self._doc_counts[i], query_tokens)
                )
                match_type = "bm25"
            if rank <= 0.0:
                continue
            ranked.append((rank, chunk, match_type))
        if not ranked:
            return []
        # Nonsense check on the RAW rank-score, before normalisation.
        max_rank = max(r for r, _, _ in ranked)
        if max_rank < self.LOW_SCORE_THRESHOLD:
            return []
        ranked.sort(key=lambda t: (-t[0], str(t[1].get("hts") or "")))
        scored = []
        for rank, chunk, match_type in ranked:
            warning = None
            if chunk.get("kind") == "tariff_line" and not chunk.get("dutiable"):
                warning = (
                    "Non-dutiable heading/suffix row — not a classification; "
                    "see the dutiable lines under this heading."
                )
            scored.append({
                "chunk": chunk,
                # Display score: max-normalised AFTER ranking.
                "score": float(max(0.0, min(1.0, rank / max_rank))),
                "match_type": match_type,
                "warning": warning,
            })

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

        # Nonsense was already decided on the RAW rank-score before
        # max-normalisation (above). Do NOT re-check here: after
        # normalisation the top hit is always 1.0, so a normalised
        # check could never fire.
        return deduped[:top_k]
