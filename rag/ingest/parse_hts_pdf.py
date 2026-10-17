"""BBox-based parser for the HTS 2026 Rev 21 PDF (Pub. 5802).

Reads ``~/workspace/user/files/2026HTSRev21_0_cgh9.pdf`` with PyMuPDF and
emits chunk dicts + row records exactly per CONTRACTS.md.

Layout facts (verified against the real PDF -- do not guess):

- A tariff table page repeats the header ``Heading/Subheading | Stat.
  Suffix | Article Description | Unit of Quantity | Rates of Duty
  (1: General, Special; 2)``. Column boundaries are learned per page
  from the header words ``General`` / ``Special`` / ``2`` /
  ``Effective``; fixed fallbacks are never used to place rates.
- Visual left-to-right order is HTS | Description | Suffix | Unit |
  General | Special | Column 2 (plus Effective Period on Chapter 99
  subchapter II pages). Chapter 99 subchapter IV (9904) pages use a
  single ``Additional Duties`` column instead, stored in
  ``general_raw``. Raw ``get_text()`` line order REVERSES some rate
  cells, so rates are NEVER taken from raw line order -- every word is
  assigned to a column by its x-coordinate (``extract_words`` ->
  ``cluster_lines`` -> ``assign_column``).
- A rate cell may wrap over several visual lines (e.g. General
  ``"17¢ each + 2.5%"`` continued as ``"+ 1¢/jewel"`` on the next line,
  or a Special program list wrapping over four lines). Continuation
  lines carry no HTS number and are folded into the open row.
- Descriptions use dotted leaders (``"Other..........."``) and
  ``(con.)`` continuation markers; both are stripped when joining.
- Rows may continue across a page boundary: the next page re-prints the
  HTS number (``9801.00.10`` spans pp. 3167-3168). A row re-opened with
  the same HTS number on the following page is merged into the pending
  row instead of being emitted twice.
- If bbox column detection fails on a page that looks like a tariff
  table (HTS-like tokens in the HTS column but no rate header), the page
  is SKIPPED with a warning count -- General and Column 2 are never
  silently swapped.

Coverage:

- GRI (p. 16), General Notes (split GN 3/4/11 by subdivision),
  chapter notes (prose before the first tariff table of a chapter),
  section notes, statistical notes, chapter endnotes (best-effort:
  raw ``1/`` / ``2/`` markers are stored on rows; endnote definition
  texts are collected per chapter in the result ``endnotes`` map).
- Chapter 99: kind ``chapter99``; Effective Period column captured on
  subchapter II (9902) pages; ``stacking`` derived from the row text
  (``cumulative`` if "in addition"/"additional"/"plus", ``in_lieu`` if
  "in lieu").
- China appendix (PDF pp. 4290-4533): kind ``appendix``,
  ``reference_only=True`` -- it is a reference list mapping HTS numbers
  to Chapter 99 headings, not tariff lines.

CLI::

    python -m rag.ingest.parse_hts_pdf --pdf <pdf> --out-chunks chunks.jsonl \\
        --out-lines lines.json
    python -m rag.ingest.parse_hts_pdf --sample --out-chunks sample.jsonl
    python -m rag.ingest.parse_hts_pdf --validate   # exits != 0 on mismatch
    python -m rag.ingest.parse_hts_pdf --pages "1-20,911-920"

``--validate`` checks KNOWN_LINES against the real PDF and fails loudly
(exit code 1) on any mismatch.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover - PyMuPDF is required
    import pymupdf as fitz  # type: ignore[no-redef]

try:
    from ..rates import parse_special
except ImportError:  # running as a script, not as a package module
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from rag.rates import parse_special  # type: ignore[no-redef]

# ---------------------------------------------------------------------------
# Provenance / constants (CONTRACTS.md)
# ---------------------------------------------------------------------------

REVISION = "2026 Rev 21"
PUBLICATION = "Pub. 5802"
PUBLISHED = "2026-10-09"
SOURCE_PDF = "2026HTSRev21_0_cgh9.pdf"
DEFAULT_PDF = Path("~/workspace/user/files").expanduser() / SOURCE_PDF

CHINA_APPENDIX_START = 4290
CHINA_APPENDIX_END = 4533

# --sample: GRI + General Note 3 + one chapter-61 page + one chapter-85
# page + Chapter 99 excerpts (subchapter II with Effective Period, and a
# subchapter III row) + the first China-appendix page.
SAMPLE_PAGES = [16, 18, 19, 20, 2125, 2875, 3311, 3989, 4290]

# Pages that carry every KNOWN_LINES row.
VALIDATE_PAGES = sorted({912, 913, 2125, 2875, 2975, 3073, 3167, 3168, 3989})

# Ground-truth rows transcribed from the PDF (verified by hand).
# "general" is matched exactly after whitespace normalization, unless
# "general_contains" is set (Chapter 99 rows carry prose in General).
# "special"/"col2": None means "do not check"; otherwise exact match
# ("special") or exact match ("col2"), except entries flagged
# "special_startswith".
KNOWN_LINES = [
    {
        "hts": "8501.51.20",
        "general": "2.5%",
        "special": "Free",
        "special_startswith": True,
        "col2": "35%",
    },
    {
        "hts": "6103.10.60",
        "general": "9.4%",
        "special": "Free",
        "special_startswith": True,
        "col2": "90%",
    },
    {
        "hts": "0101.30.00",
        "general": "6.8%",
        "special": "Free",
        "special_startswith": True,
        "col2": "15%",
    },
    {
        "hts": "0102.29.40",
        "general": "1¢/kg",
        "special": "Free",
        "special_startswith": True,
        "col2": "5.5¢/kg",
    },
    {
        "hts": "8706.00.03",
        "general": "4%",
        "special": "Free",
        "special_startswith": True,
        "col2": "25%",
    },
    {
        "hts": "9105.99.10",
        "general": "17¢ each + 2.5% + 1¢/jewel",
        "special": "Free",
        "special_startswith": True,
        "col2": "$4.50 each + 65% + 25¢/jewel",
    },
    {"hts": "9801.00.10", "general": "Free", "special": None, "col2": None},
    {
        "hts": "9903.88.01",
        "general_contains": "plus 25%",
        "special": None,
        "col2": None,
    },
]

# ---------------------------------------------------------------------------
# Geometry constants (verified against the real PDF)
# ---------------------------------------------------------------------------

HEADER_BAND = (60.0, 95.0)  # y-range of the repeated table header
BODY_TOP = 60.0  # words above this are running titles, not content
# Table pages: body rows start at y~93.7; the repeated header occupies
# y~65-86, so table bodies start at 90 to keep header words ("General",
# "Special", "2") out of the first row's cells.
TABLE_BODY_TOP = 90.0
BODY_BOTTOM = 745.0  # words below this are footers / endnote definitions
LINE_TOL = 3.0  # max y0 spread within one visual line
HTS_COL_END = 92.0
SUFFIX_COL_END = 108.0
DESC_COL_END = 335.0

HTS_RE = re.compile(r"^\d{4}(?:\.\d{2}){0,2}$")
SUFFIX_RE = re.compile(r"^\d{2}$")
FOOTNOTE_RE = re.compile(r"^\d+/$")
CHAPTER_FOOTER_RE = re.compile(r"^(\d{1,2})\s*-")
SECTION_RE = re.compile(r"^[IVXLC]+$")
GN_HEADING_RE = re.compile(r"General Note (\d+)", re.IGNORECASE)
CHAPTER_HEADING_RE = re.compile(r"\bCHAPTER\s+(\d{1,2})\b")
ENDNOTE_LINE_RE = re.compile(r"^(\d+)/\s*(.*)$")
SUBDIVISION_RE = re.compile(r"^\(([a-z])\)\s")
GN_SPLIT_NOTES = {3, 4, 11}

# Running head (line 4 of each GN page, e.g. "Singapore") -> GN number.
# From the TOC (pp.6-7): GN 25 Singapore, 26 Chile, 27 Morocco,
# 28 Australia, 29 DR-CAFTA, 30 Bahrain, 31 Oman, 32 Peru, 34 Colombia,
# 35 Panama, 36 Japan, 4 GSP, 5 Automotive, 6 Civil Aircraft, 7 CBERA,
# 8 Israel, 9 Canada (suspended), 10 Freely Associated States, 11 USMCA,
# 13 Pharmaceutical, 14 Intermediate Chemicals for Dyes, 15 Exclusions,
# 16 AGOA, 17 CBTPA, 18 Jordan. Most GNs 13-32 have no numbered heading
# line, so without this their pages inherit the previous GN's number.
RUNNING_HEAD_GN = {
    "gsp": 4, "automotive": 5, "civil aircraft": 6, "cbera": 7,
    "israel": 8, "canada": 9, "freely associated states": 10,
    "usmca": 11, "nafta": 12, "pharmaceutical products": 13,
    "pharmaceutical": 13, "dyes": 14, "exclusions": 15, "agoa": 16,
    "cbtpa": 17, "jordan": 18, "singapore": 25, "chile": 26,
    "morocco": 27, "australia": 28, "dr-cafta": 29, "bahrain": 30,
    "oman": 31, "peru": 32, "korea": 33, "colombia": 34, "panama": 35,
    "japan": 36,
}


# ---------------------------------------------------------------------------
# Word extraction / line clustering / column assignment
# ---------------------------------------------------------------------------


def extract_words(page) -> list[tuple]:
    """Return ``page.get_text("words")`` -- (x0, y0, x1, y1, text, ...).

    Takes a PyMuPDF page (or any object with ``get_text("words")``,
    which is what the tests mock). Words are returned in document order;
    callers must NOT rely on that order for column placement.
    """
    return list(page.get_text("words"))


def cluster_lines(words: list[tuple], tol: float = LINE_TOL) -> list[list[tuple]]:
    """Cluster words into visual lines by y0, each line sorted by x0.

    Words on the same visual line share (nearly) the same y0; adjacent
    lines are ~10pt apart, so ``tol`` = 3pt is safe.
    """
    ordered = sorted(words, key=lambda w: (w[1], w[0]))
    lines: list[list[tuple]] = []
    current: list[tuple] = []
    current_y: float | None = None
    for w in ordered:
        if current_y is None or abs(w[1] - current_y) <= tol:
            current.append(w)
            if current_y is None:
                current_y = w[1]
        else:
            lines.append(sorted(current, key=lambda x: x[0]))
            current = [w]
            current_y = w[1]
    if current:
        lines.append(sorted(current, key=lambda x: x[0]))
    return lines


def detect_columns(words: list[tuple]) -> dict | None:
    """Learn per-page column boundaries from the table header words.

    Returns ``None`` when no rate header can be found -- the caller must
    then skip the page, never guess.

    Two header variants exist:

    - Standard: ``General`` / ``Special`` / ``2`` (optionally
      ``Effective``). Column starts are derived from header-word x0
      positions (headers are centered over their columns, so a fixed
      inset is applied): general ~20pt left of ``General``, special
      ~28pt left of ``Special``, col2 ~25pt left of ``2``, effective
      ~14pt left of ``Effective``.
    - Chapter 99 subchapter IV (9904): a single ``Additional Duties``
      column, left-aligned with the standard General column.
    """
    band = [w for w in words if HEADER_BAND[0] <= w[1] <= HEADER_BAND[1]]

    def _header(text: str, min_x: float = 0.0) -> float | None:
        cands = [w for w in band if w[4] == text and w[0] >= min_x]
        if not cands:
            return None
        return min(cands, key=lambda w: w[1])[0]

    general_hdr = _header("General", 300.0)
    special_hdr = _header("Special", 300.0)
    if general_hdr is not None and special_hdr is not None:
        col2_hdr = _header("2", special_hdr)
        if col2_hdr is None:
            return None
        effective_hdr = _header("Effective", col2_hdr)
        return {
            "variant": "standard",
            "hts_end": HTS_COL_END,
            "suffix_end": SUFFIX_COL_END,
            "desc_end": DESC_COL_END,
            "general": general_hdr - 20.0,
            "special": special_hdr - 28.0,
            "col2": col2_hdr - 25.0,
            "effective": (effective_hdr - 14.0) if effective_hdr is not None else None,
            "has_effective": effective_hdr is not None,
        }
    # Chapter 99 subchapter IV (9904): "Additional Duties" single column.
    additional_hdr = _header("Additional", 300.0)
    duties_hdr = _header("Duties", 300.0)
    if additional_hdr is not None and duties_hdr is not None:
        return {
            "variant": "additional_duties",
            "hts_end": HTS_COL_END,
            "suffix_end": SUFFIX_COL_END,
            "desc_end": DESC_COL_END,
            "additional": min(max(additional_hdr - 70.0, 335.0), 430.0),
            "has_effective": False,
        }
    return None


def assign_column(x0: float, cols: dict) -> str:
    """Assign a word's x0 to a column: hts|suffix|desc|unit|general|special|col2|effective|additional."""
    if x0 < cols["hts_end"]:
        return "hts"
    if x0 < cols["suffix_end"]:
        return "suffix"
    if x0 < cols["desc_end"]:
        return "desc"
    if cols.get("variant") == "additional_duties":
        if x0 < cols["additional"]:
            return "unit"
        return "additional"
    if x0 < cols["general"]:
        return "unit"
    if x0 < cols["special"]:
        return "general"
    if x0 < cols["col2"]:
        return "special"
    if cols.get("has_effective") and cols.get("effective") is not None:
        if x0 < cols["effective"]:
            return "col2"
        return "effective"
    return "col2"


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------

_DOT_RUN_RE = re.compile(r"\.{2,}$")
# Tokens whose single trailing dot is part of the abbreviation, not a
# dotted leader (``"No............."`` -> ``"No."``, not ``"No"``).
_ABBREV_DOT = {"No": "No.", "Jwls": "Jwls.", "U.S": "U.S."}


def _looks_like_table(words: list[tuple]) -> bool:
    """Heuristic for a header-less page that might be a tariff table.

    True when the body has several HTS numbers in the HTS column
    (x~48) AND several rate-like tokens (``%``, ``¢``, ``Free``) in the
    rate columns (x>370). Prose pages (USMCA rules of origin, chapter
    notes) may cite HTS numbers at the left margin but carry no rate
    cells, so they fail this test and are handled as prose instead of
    being skipped.
    """
    body = [w for w in words if TABLE_BODY_TOP <= w[1] <= BODY_BOTTOM]
    hts_count = sum(1 for w in body if w[0] < 55.0 and HTS_RE.match(w[4]))
    rate_count = sum(
        1
        for w in body
        if w[0] > 370.0
        and ("%" in w[4] or "¢" in w[4] or w[4] == "Free")
    )
    return hts_count >= 3 and rate_count >= 2


def _clean_token(token: str) -> str:
    """Strip dotted leaders (``"Other..........."`` -> ``"Other"``).

    A bare ``"No"`` is left alone; only a token that actually carried a
    run of leader dots gets the abbreviation dot back (``"No...."`` ->
    ``"No."``).
    """
    if set(token) <= {"."}:
        return ""
    m = _DOT_RUN_RE.search(token)
    if not m:
        return token
    stripped = token[: m.start()]
    return _ABBREV_DOT.get(stripped, stripped)


def _join(parts: list[str], kind: str) -> str | None:
    """Join accumulated word parts for one cell, dropping leaders."""
    cleaned = []
    for tok in parts:
        if tok == "(con.)":
            continue  # layout artifact; joining already continues the text
        c = _clean_token(tok)
        if c:
            cleaned.append(c)
    if not cleaned:
        return None
    text = " ".join(cleaned)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _norm_ws(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


# ---------------------------------------------------------------------------
# Chapter / section inference from page furniture
# ---------------------------------------------------------------------------


def infer_chapter_section(words: list[tuple]) -> tuple[int | None, str | None]:
    """Infer (chapter, section) from the page footer furniture.

    The footer line near y=56 reads like ``"61-8"`` or
    ``"99 - III - 87"``; the section numeral (``"XVI"``) sits near y=47.
    Returns (None, None) when no furniture is found.
    """
    footer_words = [w for w in words if 50.0 <= w[1] <= 62.0 and w[0] < 130.0]
    chapter: int | None = None
    if footer_words:
        line = " ".join(w[4] for w in sorted(footer_words, key=lambda w: w[0]))
        m = CHAPTER_FOOTER_RE.match(line)
        if m:
            chapter = int(m.group(1))
    section = None
    for w in words:
        if 44.0 <= w[1] <= 50.0 and w[0] < 70.0 and SECTION_RE.match(w[4]):
            section = w[4]
            break
    return chapter, section

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class HTSParser:
    """Stateful bbox parser; one instance parses one PDF in page order."""

    def __init__(self) -> None:
        self.chapter: int | None = None
        self.section: str | None = None
        self.gn_number: int | None = None
        self.first_table_seen: set[int] = set()
        self.seen_ids: dict[str, int] = {}
        # HTS numbers of rows created on fully parsed earlier pages.
        # Used to suppress phantom running-head re-prints: continuation
        # pages re-print the current 8-digit HTS at the top (no rates)
        # as a section header, which must not become a duplicate row.
        self._prior_page_hts: set[str] = set()
        self.endnotes: dict[str, str] = {}  # "ch{chapter}:{marker}" -> text
        self.stats: dict = {
            "pages_parsed": 0,
            "pages_skipped": 0,
            "skipped_pages": [],
            "warnings": [],
            "dropped_preamble_lines": 0,
            "endnotes_collected": 0,
        }

    # -- ids ---------------------------------------------------------------

    def _unique_id(self, base: str) -> str:
        n = self.seen_ids.get(base, 0)
        self.seen_ids[base] = n + 1
        return base if n == 0 else f"{base}-{n + 1}"

    # -- chunk builders -----------------------------------------------------

    def _base_chunk(
        self,
        chunk_id: str,
        kind: str,
        text: str,
        page: int,
        chapter: int | None,
        section: str | None,
        reference_only: bool = False,
    ) -> dict:
        return {
            "id": chunk_id,
            "kind": kind,
            "text": text,
            "hts": None,
            "description": None,
            "unit": None,
            "general_raw": None,
            "special_raw": None,
            "col2_raw": None,
            "special_programs": [],
            "footnotes": [],
            "page": page,
            "chapter": chapter,
            "section": section,
            "revision": REVISION,
            "publication": PUBLICATION,
            "effective_period": None,
            "stacking": "unknown",
            "reference_only": reference_only,
            "dutiable": False,
        }

    def _stacking(
        self,
        chapter: int | None,
        general: str | None,
        description: str | None,
        hts: str | None = None,
    ) -> str:
        if chapter != 99:
            return "standalone"
        hay = f"{general or ''} {description or ''}".lower()
        if "in lieu" in hay:
            return "in_lieu"
        if "no additional" in hay:
            return "standalone"
        # Subchapter IV (9904) rows are additional duties by definition.
        if hts and hts.startswith("9904"):
            return "cumulative"
        # "plus" (word) or "+" (symbol) after "applicable subheading"
        # both mean the Chapter 99 duty stacks on the base rate.
        if "in addition" in hay or "additional" in hay or "plus" in hay:
            return "cumulative"
        if " + " in (general or ""):
            return "cumulative"
        return "unknown"

    def _row_to_chunk(self, row: dict) -> dict | None:
        """Convert an accumulated row to a chunk dict (None if empty)."""
        description = _join(row["desc_parts"], "desc")
        unit = _join(row["unit_parts"], "unit")
        general = _join(row["general_parts"], "general")
        special = _join(row["special_parts"], "special")
        col2 = _join(row["col2_parts"], "col2")
        effective = _join(row["effective_parts"], "effective")
        hts = row["hts"]
        if hts is None and not description and not general and not special and not col2:
            return None
        chapter = row["chapter"]
        # HTS prefix is authoritative: an isolated-page parse may not have
        # seen the chapter header, but 99xx is always Chapter 99 (and
        # 98xx always Chapter 98). Without this, a Ch.99 row parsed alone
        # gets kind tariff_line + stacking standalone instead of
        # chapter99 + cumulative.
        if hts and hts.startswith("99"):
            chapter = 99
        elif hts and hts.startswith("98"):
            chapter = 98
        kind = "chapter99" if chapter == 99 else "tariff_line"
        text_bits = []
        if hts:
            text_bits.append(hts)
        if description:
            text_bits.append(description)
        if unit:
            text_bits.append(f"Unit: {unit}")
        if general:
            text_bits.append(f"General: {general}")
        if special:
            text_bits.append(f"Special: {special}")
        if col2:
            text_bits.append(f"Col2: {col2}")
        if effective:
            text_bits.append(f"Effective Period: {effective}")
        chunk = self._base_chunk(
            self._unique_id(f"hts:{hts}" if hts else "hts:unknown"),
            kind,
            " | ".join(text_bits),
            row["page"],
            chapter,
            row["section"],
        )
        chunk.update(
            {
                "hts": hts,
                "description": description,
                "unit": unit,
                "general_raw": general,
                "special_raw": special,
                "col2_raw": col2,
                "footnotes": sorted(set(row["footnotes"])),
                "effective_period": effective,
                "stacking": self._stacking(chapter, general, description, hts),
                "dutiable": row["dutiable"],
            }
        )
        if special:
            chunk["special_programs"] = [
                {"symbol": p["symbol"], "rate_raw": p["rate"]["raw"]}
                for p in parse_special(special)["programs"]
            ]
        return chunk

    # -- table pages ---------------------------------------------------------

    def _new_row(
        self, page: int, chapter: int | None, section: str | None
    ) -> dict:
        return {
            "hts": None,
            "hts_col": None,
            "from_hts_token": False,
            "suffix": None,
            "desc_parts": [],
            "unit_parts": [],
            "general_parts": [],
            "special_parts": [],
            "col2_parts": [],
            "effective_parts": [],
            "footnotes": [],
            "page": page,
            "chapter": chapter,
            "section": section,
            "dutiable": False,
        }

    def _parse_table_page(
        self, words: list[tuple], page_no: int, pending: dict | None
    ) -> tuple[list[dict], dict | None]:
        """Parse one tariff-table page; returns (chunks, pending_row)."""
        cols = detect_columns(words)
        body = [w for w in words if TABLE_BODY_TOP <= w[1] <= BODY_BOTTOM]
        if cols is None:
            # Table-like page but no rate header: skip loudly rather
            # than risk swapping General/Col2. Prose pages fail
            # _looks_like_table and fall through to prose handling.
            if _looks_like_table(words):
                self.stats["pages_skipped"] += 1
                self.stats["skipped_pages"].append(page_no)
                self.stats["warnings"].append(
                    f"p.{page_no}: column detection failed; page skipped"
                )
                return [], pending
            # Not actually a table page: fall through to prose handling.
            return self._parse_prose_page(words, page_no, pending)

        if self.chapter is not None:
            self.first_table_seen.add(self.chapter)

        chunks: list[dict] = []
        current = pending
        parent_hts: str | None = (
            pending["hts_col"] if pending is not None else None
        )
        parent_8: str | None = None
        if pending is not None and pending.get("hts"):
            m8 = re.match(r"^(\d{4}\.\d{2}\.\d{2})", pending["hts"])
            parent_8 = m8.group(1) if m8 else None
        # Recompute the live parent from the pending row when possible.
        if pending is not None and pending.get("hts_col"):
            parent_hts = pending["hts_col"]

        def flush() -> None:
            nonlocal current
            if current is not None:
                chunk = self._row_to_chunk(current)
                if chunk is not None:
                    chunks.append(chunk)
                current = None

        lines = cluster_lines(body)
        page_hts: set[str] = set()
        first_row_start = True
        for line in lines:
            buckets: dict[str, list[tuple]] = {}
            for w in line:
                buckets.setdefault(assign_column(w[0], cols), []).append(w)
            hts_tokens = [w[4] for w in buckets.get("hts", [])]
            hts_match = next(
                (t for t in hts_tokens if HTS_RE.match(t)), None
            )
            suffix_tokens = [w[4] for w in buckets.get("suffix", [])]
            suffix_val = next(
                (t for t in suffix_tokens if SUFFIX_RE.match(t)), None
            )
            desc_words = buckets.get("desc", [])
            # Footnote markers live in the suffix or unit column.
            markers = [
                w[4]
                for col in ("suffix", "unit", "general", "special", "col2", "additional")
                for w in buckets.get(col, [])
                if FOOTNOTE_RE.match(w[4])
            ]
            # A line starts a new row if it carries an HTS number, or a
            # 2-digit statistical suffix plus a description (a 10-digit
            # statistical subdivision row).
            is_row_start = hts_match is not None or (
                suffix_val is not None and bool(desc_words)
            )
            if is_row_start:
                # Cross-page continuation: the new page re-prints the
                # pending row's HTS number -- merge, do not duplicate.
                # (Requires from_hts_token: a pending statistical
                # suffix row shares its parent's hts_col but is not
                # the parent row itself.)
                if (
                    current is not None
                    and hts_match is not None
                    and current.get("from_hts_token")
                    and current.get("hts_col") == hts_match
                    and current.get("page") != page_no
                ):
                    first_row_start = False
                    self._append_line(current, buckets, markers, cols)
                    continue
                # "(con.)" re-print: a continuation page re-prints a
                # heading / subheading / subheading line with a
                # "(con.)" marker (in the HTS or description column)
                # and no rate words. It duplicates an HTS from an
                # earlier page and is not a row: suppress it, but adopt
                # it as the parent for following suffix rows.
                has_con = "(con.)" in hts_tokens or "(con.)" in [
                    w[4] for w in desc_words
                ]
                rate_words_present = any(
                    buckets.get(col)
                    for col in ("unit", "general", "special", "col2", "additional")
                )
                if (
                    hts_match is not None
                    and has_con
                    and hts_match in self._prior_page_hts
                    and not rate_words_present
                ):
                    # NOTE: do NOT consume first_row_start here. A
                    # "(con.)" line is not a real row, and the
                    # re-printed 8-digit line's own "(con.)" often sits
                    # on the FOLLOWING line (e.g. 6110.20.10 on p.2172),
                    # which the first-row phantom check below must still
                    # catch.
                    flush()
                    parent_hts = hts_match
                    parent_8 = (
                        hts_match if hts_match.count(".") == 2 else None
                    )
                    continue
                # Phantom running head: a continuation page re-prints
                # the current 8-digit HTS at the top (no rates) as a
                # section header. It duplicates an HTS from an earlier
                # page and is not a row: suppress it, but adopt it as
                # the parent for following suffix rows.
                if (
                    first_row_start
                    and hts_match is not None
                    and hts_match in self._prior_page_hts
                ):
                    first_row_start = False
                    flush()
                    parent_hts = hts_match
                    parent_8 = hts_match if hts_match.count(".") == 2 else None
                    continue
                first_row_start = False
                flush()
                current = self._new_row(page_no, self.chapter, self.section)
                if hts_match is not None:
                    current["hts"] = hts_match
                    current["hts_col"] = hts_match
                    current["from_hts_token"] = True
                    page_hts.add(hts_match)
                    parent_hts = hts_match
                    if hts_match.count(".") == 2:
                        parent_8 = hts_match
                        current["dutiable"] = True
                    else:
                        # Heading (4-digit) or subheading (6-digit) row:
                        # not dutiable itself; reset the 8-digit parent.
                        parent_8 = None
                        current["dutiable"] = False
                    if suffix_val is not None:
                        current["suffix"] = suffix_val
                else:
                    # 10-digit statistical subdivision row.
                    current["suffix"] = suffix_val
                    current["hts_col"] = parent_hts
                    if parent_8 is not None:
                        current["hts"] = parent_8 + suffix_val
                    current["dutiable"] = False
                self._append_line(current, buckets, markers, cols)
            elif current is not None:
                self._append_line(current, buckets, markers, cols)
            else:
                # Preamble text before the first row on this page.
                self.stats["dropped_preamble_lines"] += 1
        self._prior_page_hts |= page_hts
        return chunks, current

    def _append_line(
        self, row: dict, buckets: dict[str, list[tuple]], markers: list[str], cols: dict
    ) -> None:
        """Fold one visual line's words into the open row, by column."""
        row["footnotes"].extend(markers)
        for col, key in (
            ("desc", "desc_parts"),
            ("unit", "unit_parts"),
            ("general", "general_parts"),
            ("additional", "general_parts"),  # 9904: single rate column
            ("special", "special_parts"),
            ("col2", "col2_parts"),
            ("effective", "effective_parts"),
        ):
            for w in buckets.get(col, []):
                if FOOTNOTE_RE.match(w[4]):
                    continue
                row[key].append(w[4])

    # -- prose pages ---------------------------------------------------------

    def _parse_prose_page(
        self, words: list[tuple], page_no: int, pending: dict | None
    ) -> tuple[list[dict], dict | None]:
        """Parse one non-table page into prose chunks.

        Any pending table row is flushed first: rows do not continue
        across a prose page.
        """
        chunks: list[dict] = []
        if pending is not None:
            chunk = self._row_to_chunk(pending)
            if chunk is not None:
                chunks.append(chunk)
            pending = None

        body = [w for w in words if BODY_TOP <= w[1] <= BODY_BOTTOM]
        lines = cluster_lines(body)
        line_texts = [
            (line[0][0], " ".join(w[4] for w in line)) for line in lines if line
        ]
        text = "\n".join(t for _, t in line_texts)
        if not text.strip():
            return chunks, None

        chapter = self.chapter
        section = self.section

        # China appendix (reference list, not tariff lines).
        if CHINA_APPENDIX_START <= page_no <= CHINA_APPENDIX_END:
            chunk = self._base_chunk(
                self._unique_id(f"appendix:china-p{page_no}"),
                "appendix",
                text,
                page_no,
                None,
                None,
                reference_only=True,
            )
            chunk["stacking"] = "unknown"
            chunks.append(chunk)
            return chunks, None
        if page_no == CHINA_APPENDIX_END + 1:  # Change Record
            chunk = self._base_chunk(
                self._unique_id(f"appendix:change-record-p{page_no}"),
                "appendix",
                text,
                page_no,
                None,
                None,
            )
            chunks.append(chunk)
            return chunks, None

        # GRI (p. 16).
        if page_no == 16 or text.startswith("GENERAL RULES OF INTERPRETATION"):
            chunk = self._base_chunk(
                self._unique_id(f"gri:p{page_no}"), "gri", text,
                page_no, None, section,
            )
            chunks.append(chunk)
            return chunks, None

        # Endnote pages (best-effort collection).
        if "Endnotes" in text:
            chunk = self._base_chunk(
                self._unique_id(
                    f"endnotes:ch{chapter}" if chapter else f"endnotes:p{page_no}"
                ),
                "chapter_note",
                text,
                page_no,
                chapter,
                section,
            )
            chunks.append(chunk)
            return chunks, None

        if re.search(r"SECTION\s+[IVXLC]+\s+NOTES", text, re.IGNORECASE):
            chunk = self._base_chunk(
                self._unique_id(
                    f"section-note:ch{chapter}-p{page_no}" if chapter else f"section-note:p{page_no}"
                ),
                "section_note",
                text,
                page_no,
                chapter,
                section,
            )
            chunks.append(chunk)
            return chunks, None

        if re.search(r"Statistical Notes?", text):
            chunk = self._base_chunk(
                self._unique_id(
                    f"stat-note:ch{chapter}-p{page_no}" if chapter else f"stat-note:p{page_no}"
                ),
                "statistical_note",
                text,
                page_no,
                chapter,
                section,
            )
            chunks.append(chunk)
            return chunks, None

        # General Notes / front matter (no chapter yet).
        if chapter is None and page_no < 1000:
            # Running-head mapping (see RUNNING_HEAD_GN). The running
            # head is a short exact line ("Singapore", "DR-CAFTA") in the
            # header block. Match EXACT short lines only: body lines in
            # the first lines (e.g. p.182 listing "Dominican Republic,
            # Costa Rica, Panama" among former CBTPA countries) must
            # never match.
            if page_no >= 16:
                for _x0, _line in line_texts[:6]:
                    _lh = _line.strip().lower()
                    if len(_lh) > 24:
                        continue
                    _num = RUNNING_HEAD_GN.get(_lh)
                    if _num is not None and _num > (self.gn_number or 0):
                        self.gn_number = _num
                        break
            # GN heading lines in the body read "4. Products of Countries
            # ..." / "33. United States-Korea ..." — NOT "General Note 4".
            # The old code only matched the literal "General Note N" header
            # (present on pp.17-18), so gn_number stuck at 3 for the whole
            # GN section and GN 4 content was mislabelled gn:3-*.
            # Discriminator: a GN heading is a titled line. In-note
            # numbered rules (GN 11/25/26 subdivision (o) tariff-shift
            # rules, numbered up to ~100) say "A change to ...", "No
            # change ...", "For any ...", etc. — never a title. So: a
            # heading is a numbered line in the first lines of the page
            # whose text is a title (no rule markers), with number 1..36
            # (there are only 36 General Notes), monotonically increasing.
            # Headings sit right under the running head (first ~8 lines);
            # rule lists start lower. Scanning deeper only admits rules.
            for _x0, _line in line_texts[:8]:
                _t = _line.strip()
                _hm = re.match(r"^(\d{1,2})\.\s+(.+)$", _t)
                if not _hm:
                    continue
                _n = int(_hm.group(1))
                _rest = _hm.group(2)
                if _n < 1 or _n > 36:
                    continue
                if _n <= (self.gn_number or 0):
                    continue
                _low = _rest.lower()
                if any(k in _low for k in (
                        "a change to", "change to ", "no change",
                        "percent where", "subheading rule",
                        "chapter rule", "heading rule",
                        "change in tariff classification",
                        "change in classification")):
                    continue
                # Rule lines begin with function words / brackets;
                # headings begin with proper-noun titles.
                if _rest.strip().startswith("["):
                    continue
                if re.match(r"^(for |for the purposes|if |whenever |the |a |an |in |on |to |of |no |any |each |all |such |this |that |these |those |it |its |where |when |while |provided |except |subject |notwithstanding |not |goods |good )", _low):
                    continue
                self.gn_number = _n
                break
            m = GN_HEADING_RE.search(text)
            if m:
                self.gn_number = int(m.group(1))
            gn = self.gn_number
            if page_no == 17 or text.startswith("ADDITIONAL U.S. RULES"):
                chunk = self._base_chunk(
                    self._unique_id("gn:additional-rules"),
                    "general_note",
                    text,
                    page_no,
                    None,
                    section,
                )
                chunks.append(chunk)
                return chunks, None
            if gn in GN_SPLIT_NOTES:
                chunks.extend(
                    self._split_gn_page(line_texts, gn, page_no, section)
                )
                return chunks, None
            chunk = self._base_chunk(
                self._unique_id(f"gn:{gn}-p{page_no}" if gn else f"gn:p{page_no}"),
                "general_note",
                text,
                page_no,
                None,
                section,
            )
            chunks.append(chunk)
            return chunks, None

        # Chapter notes (incl. Chapter 99 U.S. Notes and any other prose
        # inside a chapter, e.g. text before its first tariff table).
        chunk = self._base_chunk(
            self._unique_id(
                f"note:ch{chapter}-p{page_no}" if chapter else f"note:p{page_no}"
            ),
            "chapter_note",
            text,
            page_no,
            chapter,
            section,
        )
        chunks.append(chunk)
        return chunks, None

    def _split_gn_page(
        self,
        line_texts: list[tuple[float, str]],
        gn: int,
        page_no: int,
        section: str | None,
    ) -> list[dict]:
        """Split a GN 3/4/11 page by top-level ``(a)`` subdivisions.

        Best-effort: a boundary is a line starting with ``(letter)`` whose
        first word sits at the top-level indent (x0 < 75). The PDF's
        indentation ladder is ~65 (top level), ~83 (``(i)``), ~101
        (``(A)``), ~119 (``(1)``), ~137 (``(I)``), so nested markers do
        not split.
        """
        segments: list[tuple[str | None, list[str]]] = []
        current_label: str | None = None
        current_lines: list[str] = []
        for x0, t in line_texts:
            m = SUBDIVISION_RE.match(t)
            if m and x0 < 75.0 and current_lines:
                segments.append((current_label, current_lines))
                current_label = m.group(1)
                current_lines = [t]
            else:
                current_lines.append(t)
        segments.append((current_label, current_lines))
        out: list[dict] = []
        for label, seg_lines in segments:
            if not any(t.strip() for t in seg_lines):
                continue
            seg_id = f"gn:{gn}-{label}" if label else f"gn:{gn}-p{page_no}"
            out.append(
                self._base_chunk(
                    self._unique_id(seg_id),
                    "general_note",
                    "\n".join(seg_lines),
                    page_no,
                    None,
                    section,
                )
            )
        return out

    # -- per-page driver ------------------------------------------------------

    def parse_page(self, page, page_no: int, pending: dict | None = None):
        """Parse one PDF page; returns (chunks, pending_row)."""
        words = extract_words(page)
        self._collect_endnotes(words)
        ch, sec = infer_chapter_section(words)
        if ch is not None:
            self.chapter = ch
        if sec is not None:
            self.section = sec
        if CHINA_APPENDIX_START <= page_no <= CHINA_APPENDIX_END + 1:
            self.chapter = None
            self.section = None
        self.stats["pages_parsed"] += 1

        # Appendix / Change Record pages are prose by page range, even
        # though their two-column lists start HTS-like tokens near x=46.
        if CHINA_APPENDIX_START <= page_no <= CHINA_APPENDIX_END + 1:
            return self._parse_prose_page(words, page_no, pending)

        # Decide table vs prose: a table page carries the rate header.
        if detect_columns(words) is not None:
            return self._parse_table_page(words, page_no, pending)
        # Header-less page: prose, unless it looks like a table page
        # (handled inside _parse_table_page's skip logic).
        if _looks_like_table(words):
            return self._parse_table_page(words, page_no, pending)
        return self._parse_prose_page(words, page_no, pending)

    def _collect_endnotes(self, words: list[tuple]) -> None:
        """Best-effort: collect footnote-definition lines from the footer.

        Footer lines below the body that start with ``1/`` / ``2/`` are
        endnote definitions (e.g. ``1/ The quantities ...``). Stored in
        ``self.endnotes`` keyed ``"ch{chapter}:{marker}"``.
        """
        footer = [w for w in words if w[1] > BODY_BOTTOM]
        for line in cluster_lines(footer):
            text = " ".join(w[4] for w in line)
            m = ENDNOTE_LINE_RE.match(text.strip())
            if m:
                key = f"ch{self.chapter}:{m.group(1)}/" if self.chapter else f"p?:{m.group(1)}/"
                if key not in self.endnotes:
                    self.endnotes[key] = m.group(2).strip()
                    self.stats["endnotes_collected"] += 1

# ---------------------------------------------------------------------------
# Whole-PDF driver / validation / CLI
# ---------------------------------------------------------------------------


def parse_pages_spec(spec: str) -> list[int]:
    """Parse a page spec like ``"1-20,911-920"`` into sorted page numbers."""
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            pages.extend(range(int(lo), int(hi) + 1))
        else:
            pages.append(int(part))
    return sorted(set(pages))


def parse_pdf(
    pdf_path: str | Path = DEFAULT_PDF,
    pages: list[int] | None = None,
) -> dict:
    """Parse the HTS PDF (all pages, or ``pages`` in ascending order).

    Returns ``{"chunks", "lines", "stats", "endnotes"}`` where ``lines``
    is the subset of chunks that are tariff rows (``tariff_line`` /
    ``chapter99``). When ``pages`` is a subset, cross-page row merging
    still applies within the parsed run.
    """
    parser = HTSParser()
    chunks: list[dict] = []
    pending: dict | None = None
    with fitz.open(str(pdf_path)) as doc:
        total = len(doc)
        wanted = pages if pages is not None else list(range(1, total + 1))
        for i, page_no in enumerate(wanted):
            if page_no < 1 or page_no > total:
                parser.stats["warnings"].append(
                    f"p.{page_no}: out of range (PDF has {total} pages); skipped"
                )
                continue
            page_chunks, pending = parser.parse_page(doc[page_no - 1], page_no, pending)
            chunks.extend(page_chunks)
            if (i + 1) % 500 == 0:
                print(
                    f"... parsed {i + 1}/{len(wanted)} pages",
                    file=sys.stderr,
                )
    if pending is not None:
        chunk = parser._row_to_chunk(pending)
        if chunk is not None:
            chunks.append(chunk)
    # 6-digit rows are dutiable when no 8-digit row shares their prefix.
    eight_prefixes = {
        c["hts"][:7]
        for c in chunks
        if c.get("hts") and re.match(r"^\d{4}\.\d{2}\.\d{2}", c["hts"])
    }
    for c in chunks:
        if c.get("hts") and re.match(r"^\d{4}\.\d{2}$", c["hts"]):
            if c["hts"] not in eight_prefixes:
                c["dutiable"] = True
    lines = [c for c in chunks if c["kind"] in ("tariff_line", "chapter99")]
    return {
        "chunks": chunks,
        "lines": lines,
        "stats": parser.stats,
        "endnotes": parser.endnotes,
    }


def validate_chunks(chunks: list[dict]) -> list[tuple[str, bool, str]]:
    """Check KNOWN_LINES against parsed chunks.

    Returns a list of ``(hts, ok, detail)``; callers fail loudly when
    any entry is not ok.
    """
    by_hts: dict[str, dict] = {}
    for c in chunks:
        if c.get("hts") and c["kind"] in ("tariff_line", "chapter99"):
            by_hts.setdefault(c["hts"], c)
    results: list[tuple[str, bool, str]] = []
    for expected in KNOWN_LINES:
        hts = expected["hts"]
        chunk = by_hts.get(hts)
        if chunk is None:
            results.append((hts, False, "no chunk with this HTS"))
            continue
        problems: list[str] = []
        if "general" in expected and expected["general"] is not None:
            if _norm_ws(chunk.get("general_raw")) != _norm_ws(expected["general"]):
                problems.append(
                    f"general={chunk.get('general_raw')!r} != {expected['general']!r}"
                )
        if expected.get("general_contains"):
            if expected["general_contains"] not in (chunk.get("general_raw") or ""):
                problems.append(
                    f"general={chunk.get('general_raw')!r} lacks {expected['general_contains']!r}"
                )
        if expected.get("special") is not None:
            actual = chunk.get("special_raw") or ""
            if expected.get("special_startswith"):
                if not actual.startswith(expected["special"]):
                    problems.append(
                        f"special={actual!r} does not start with {expected['special']!r}"
                    )
            elif _norm_ws(actual) != _norm_ws(expected["special"]):
                problems.append(
                    f"special={actual!r} != {expected['special']!r}"
                )
        if expected.get("col2") is not None:
            if _norm_ws(chunk.get("col2_raw")) != _norm_ws(expected["col2"]):
                problems.append(
                    f"col2={chunk.get('col2_raw')!r} != {expected['col2']!r}"
                )
        if problems:
            results.append((hts, False, "; ".join(problems)))
        else:
            results.append((hts, True, f"p.{chunk.get('page')} OK"))
    return results


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns a process exit code."""
    ap = argparse.ArgumentParser(
        description="BBox-based parser for the HTS 2026 Rev 21 PDF."
    )
    ap.add_argument("--pdf", default=str(DEFAULT_PDF), help="Path to the HTS PDF")
    ap.add_argument("--out-chunks", default=None, help="Write chunks as JSONL")
    ap.add_argument("--out-lines", default=None, help="Write tariff rows as JSON")
    ap.add_argument(
        "--pages", default=None, help='Page range, e.g. "1-20,911-920"'
    )
    ap.add_argument(
        "--sample",
        action="store_true",
        help="Parse the sample set (GRI + GN3 + ch61 + ch85 + ch99 excerpts)",
    )
    ap.add_argument(
        "--validate",
        action="store_true",
        help="Check KNOWN_LINES against the PDF; exit != 0 on mismatch",
    )
    args = ap.parse_args(argv)

    if args.validate:
        pages = VALIDATE_PAGES
    elif args.sample:
        pages = SAMPLE_PAGES
    elif args.pages:
        pages = parse_pages_spec(args.pages)
    else:
        pages = None

    result = parse_pdf(args.pdf, pages)
    chunks, lines, stats = result["chunks"], result["lines"], result["stats"]

    if args.out_chunks:
        with open(args.out_chunks, "w", encoding="utf-8") as f:
            for c in chunks:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        print(f"Wrote {len(chunks)} chunks to {args.out_chunks}")
    if args.out_lines:
        with open(args.out_lines, "w", encoding="utf-8") as f:
            json.dump(lines, f, ensure_ascii=False, indent=1)
        print(f"Wrote {len(lines)} lines to {args.out_lines}")

    print(
        f"pages_parsed={stats['pages_parsed']} chunks={len(chunks)} "
        f"lines={len(lines)} skipped={stats['pages_skipped']} "
        f"endnotes={stats['endnotes_collected']}"
    )
    for w in stats["warnings"]:
        print(f"WARNING: {w}", file=sys.stderr)
    if stats["skipped_pages"]:
        print(f"Skipped pages: {stats['skipped_pages']}", file=sys.stderr)

    if args.validate:
        failures = 0
        for hts, ok, detail in validate_chunks(chunks):
            print(f"{'PASS' if ok else 'FAIL'} {hts}: {detail}")
            failures += 0 if ok else 1
        if failures:
            print(f"VALIDATION FAILED: {failures} mismatch(es)", file=sys.stderr)
            return 1
        print("VALIDATION PASSED: all KNOWN_LINES match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
