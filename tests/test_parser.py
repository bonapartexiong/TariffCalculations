"""Tests for rag/ingest/parse_hts_pdf.py.

Synthetic bbox fixtures (mock fitz pages) prove column ordering: fixture
words are deliberately supplied in REVERSED order, so any parser that
trusted raw ``get_text()`` order would swap General/Special/Column 2.
Real-PDF tests run only when the PDF is present, else they skip.
"""

import pytest

from rag.ingest.parse_hts_pdf import (
    DEFAULT_PDF,
    KNOWN_LINES,
    SAMPLE_PAGES,
    VALIDATE_PAGES,
    HTSParser,
    assign_column,
    detect_columns,
    parse_pdf,
    validate_chunks,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


class FakePage:
    """Minimal stand-in for a PyMuPDF page (only get_text("words"))."""

    def __init__(self, words):
        self._words = words

    def get_text(self, kind):
        assert kind == "words"
        return list(self._words)


def W(x, y, text, w=20.0):
    """One word tuple: (x0, y0, x1, y1, text, block, line, word_no)."""
    return (x, y, x + w, y + 10.0, text, 0, 0, 0)


def header_words(general_x=398.7, special_x=471.3, col2_x=548.1, effective_x=None):
    words = [
        W(general_x, 85.1, "General"),
        W(special_x, 85.1, "Special"),
        W(col2_x, 76.2, "2"),
    ]
    if effective_x is not None:
        words.append(W(effective_x, 70.5, "Effective"))
    return words


def footer_words(chapter_token, section="XVI"):
    return [
        W(45.7, 47.3, section, 12.0),
        W(45.7, 55.7, chapter_token, 18.0),
    ]


def shuffled(words):
    """Reverse the word order: raw get_text() order must not matter."""
    return list(reversed(words))


# ---------------------------------------------------------------------------
# Column detection / assignment
# ---------------------------------------------------------------------------


def test_detect_columns_none_without_header():
    words = [W(47.8, 100.0, "8501.51.20"), W(110.7, 100.0, "Other")]
    assert detect_columns(words) is None


def test_detect_columns_learns_thresholds():
    cols = detect_columns(header_words())
    assert cols is not None
    assert cols["general"] == pytest.approx(398.7 - 20.0)
    assert cols["special"] == pytest.approx(471.3 - 28.0)
    assert cols["col2"] == pytest.approx(548.1 - 25.0)
    assert cols["has_effective"] is False


def test_assign_column_boundaries():
    cols = detect_columns(header_words())
    assert assign_column(47.8, cols) == "hts"
    assert assign_column(95.7, cols) == "suffix"
    assert assign_column(150.0, cols) == "desc"
    assert assign_column(337.5, cols) == "unit"
    assert assign_column(380.7, cols) == "general"
    assert assign_column(445.5, cols) == "special"
    assert assign_column(524.0, cols) == "col2"


# ---------------------------------------------------------------------------
# Synthetic table pages (words in reversed order)
# ---------------------------------------------------------------------------


def _motor_row_page():
    """8501.51.20-style row: General 2.5%, Special Free (...), Col2 35%."""
    words = header_words() + footer_words("85-12")
    words += [
        W(47.8, 134.5, "8501.51.20"),
        W(153.9, 134.5, "Exceeding"),
        W(337.5, 134.7, ".................."),
        W(380.7, 134.7, "2.5%"),
        W(445.5, 134.7, "Free"),
        W(464.6, 134.7, "(A,"),
        W(477.6, 134.7, "AU,"),
        W(493.9, 134.7, "B,"),
        W(504.0, 134.7, "BH,"),
        W(524.0, 134.7, "35%"),
        # Special program list wraps onto the next visual line.
        W(450.5, 144.9, "CL,"),
        W(465.8, 144.9, "CO)"),
    ]
    return FakePage(shuffled(words))


def test_table_row_column_ordering_reversed_input():
    parser = HTSParser()
    chunks, pending = parser.parse_page(_motor_row_page(), 2875)
    assert pending is not None  # last row stays open until flushed
    chunks, _ = parser.parse_page(FakePage([]), 2876, pending)
    assert len(chunks) == 1
    c = chunks[0]
    assert c["hts"] == "8501.51.20"
    assert c["general_raw"] == "2.5%"
    assert c["special_raw"] == "Free (A, AU, B, BH, CL, CO)"
    assert c["col2_raw"] == "35%"
    assert c["kind"] == "tariff_line"
    assert c["dutiable"] is True
    assert c["chapter"] == 85
    assert [p["symbol"] for p in c["special_programs"]] == [
        "A", "AU", "B", "BH", "CL", "CO",
    ]


def _compound_wrap_page():
    """9105.99.10-style row: General + Col2 wrap over three lines."""
    words = header_words() + footer_words("91-5")
    words += [
        W(47.8, 224.0, "9105.99.10"),
        W(95.7, 224.0, "00"),
        W(154.0, 224.0, "Standard"),
        W(337.5, 234.0, "No............."),
        W(380.7, 234.0, "17¢"),
        W(397.0, 234.0, "each"),
        W(417.0, 234.0, "+"),
        W(423.0, 234.0, "2.5%"),
        W(445.5, 234.0, "Free"),
        W(464.6, 234.0, "(A)"),
        W(524.0, 234.0, "$4.50"),
        W(547.0, 234.0, "each"),
        W(567.0, 234.0, "+"),
        W(337.5, 244.0, "Jwls."),
        W(386.0, 244.0, "+"),
        W(393.0, 244.0, "1¢/jewel"),
        W(529.0, 244.0, "65%"),
        W(548.0, 244.0, "+"),
        W(529.0, 254.0, "25¢/jewel"),
    ]
    return FakePage(shuffled(words))


def test_compound_rate_wraps_across_lines():
    parser = HTSParser()
    chunks, pending = parser.parse_page(_compound_wrap_page(), 3073)
    assert pending is not None
    chunks, _ = parser.parse_page(FakePage([]), 3074, pending)
    assert len(chunks) == 1
    c = chunks[0]
    assert c["general_raw"] == "17¢ each + 2.5% + 1¢/jewel"
    assert c["col2_raw"] == "$4.50 each + 65% + 25¢/jewel"
    assert c["unit"] == "No. Jwls."


def _suffix_rows_page():
    """8-digit row plus a 10-digit statistical subdivision row."""
    words = header_words() + footer_words("1-2")
    words += [
        W(47.8, 114.1, "0101.21.00"),
        W(95.7, 114.1, "00"),
        W(139.5, 114.1, "Purebred"),
        W(380.7, 114.3, "Free"),
        W(524.0, 114.3, "Free"),
        W(95.7, 124.5, "10"),
        W(153.9, 124.5, "Males"),
        W(337.5, 124.7, "No."),
    ]
    return FakePage(shuffled(words))


def test_statistical_suffix_row():
    parser = HTSParser()
    chunks1, pending = parser.parse_page(_suffix_rows_page(), 912)
    assert pending is not None
    chunks2, _ = parser.parse_page(FakePage([]), 913, pending)
    chunks = chunks1 + chunks2
    by_hts = {c["hts"]: c for c in chunks}
    assert by_hts["0101.21.00"]["dutiable"] is True
    assert by_hts["0101.21.0010"]["dutiable"] is False
    assert by_hts["0101.21.0010"]["description"] == "Males"
    assert by_hts["0101.21.0010"]["unit"] == "No."


def _ch99_page():
    """9903.88.01-style row: prose General, cumulative stacking."""
    words = header_words() + footer_words("99 - III - 87", "XXII")
    words += [
        W(47.8, 449.9, "9903.88.01"),
        W(110.7, 449.9, "Except"),
        W(380.7, 522.0, "The"),
        W(397.3, 522.0, "duty"),
        W(383.7, 532.2, "provided"),
        W(390.0, 532.2, "in"),
        W(400.0, 532.2, "the"),
        W(383.7, 542.4, "applicable"),
        W(383.7, 552.6, "subheading"),
        W(427.5, 552.6, "plus"),
        W(383.7, 562.8, "25%"),
    ]
    return FakePage(shuffled(words))


def test_chapter99_stacking_cumulative():
    parser = HTSParser()
    chunks, pending = parser.parse_page(_ch99_page(), 3989)
    assert pending is not None
    chunks, _ = parser.parse_page(FakePage([]), 3990, pending)
    assert len(chunks) == 1
    c = chunks[0]
    assert c["kind"] == "chapter99"
    assert c["general_raw"] == "The duty provided in the applicable subheading plus 25%"
    assert c["stacking"] == "cumulative"


def _ch99_effective_page():
    """9902-style page with an Effective Period column."""
    words = header_words(
        general_x=390.0, special_x=437.6, col2_x=493.8, effective_x=533.0
    ) + footer_words("99 - II - 2", "XXII")
    words += [
        W(47.8, 93.7, "9902.01.01"),
        W(110.7, 93.7, "Frozen"),
        W(380.7, 104.6, "Free"),
        W(427.5, 104.6, "No"),
        W(440.4, 104.6, "change"),
        W(474.3, 104.6, "No"),
        W(487.2, 104.6, "change"),
        W(521.1, 104.6, "On"),
        W(534.5, 104.6, "or"),
        W(544.0, 104.6, "before"),
        W(524.1, 114.8, "12/31/2020"),
    ]
    return FakePage(shuffled(words))


def test_chapter99_effective_period_column():
    parser = HTSParser()
    chunks, pending = parser.parse_page(_ch99_effective_page(), 3311)
    assert pending is not None
    chunks, _ = parser.parse_page(FakePage([]), 3312, pending)
    assert len(chunks) == 1
    c = chunks[0]
    assert c["kind"] == "chapter99"
    assert c["general_raw"] == "Free"
    assert c["special_raw"] == "No change"
    assert c["col2_raw"] == "No change"
    assert c["effective_period"] == "On or before 12/31/2020"


def test_skip_page_when_columns_undetectable():
    """A header-less page that LOOKS like a table is skipped, not guessed."""
    words = []
    for i, hts in enumerate(["8501.51.20", "8501.51.40", "8501.51.60"]):
        y = 100.0 + i * 20.0
        words += [
            W(47.8, y, hts),
            W(110.7, y, "Other"),
            W(380.7, y, "2.5%"),
            W(524.0, y, "35%"),
        ]
    parser = HTSParser()
    chunks, pending = parser.parse_page(FakePage(words), 9999)
    assert chunks == []
    assert pending is None
    assert parser.stats["pages_skipped"] == 1
    assert 9999 in parser.stats["skipped_pages"]


def test_prose_page_with_hts_citations_not_skipped():
    """A prose page citing HTS numbers (no rate cells) is kept as prose."""
    words = [
        W(47.8, 100.0, "8413.82"),
        W(90.0, 100.0, "change"),
        W(47.8, 200.0, "8414.80"),
        W(90.0, 200.0, "change"),
    ]
    parser = HTSParser()
    chunks, pending = parser.parse_page(FakePage(words), 116)
    assert parser.stats["pages_skipped"] == 0
    assert len(chunks) == 1
    assert chunks[0]["kind"] == "general_note"  # front-matter prose


def test_cross_page_row_merge():
    page_a = FakePage(
        shuffled(
            header_words()
            + footer_words("98-I-2", "XXII")
            + [
                W(47.8, 93.7, "9801.00.10"),
                W(110.7, 93.7, "Products"),
                W(380.7, 134.7, "Free"),
            ]
        )
    )
    page_b = FakePage(
        shuffled(
            header_words()
            + footer_words("98-I-2", "XXII")
            + [
                W(47.8, 93.7, "9801.00.10"),  # re-printed HTS on next page
                W(47.8, 103.9, "(con.)"),
                W(110.7, 103.9, "advanced"),
                W(120.6, 103.9, "in"),
                W(139.4, 103.9, "value"),
            ]
        )
    )
    parser = HTSParser()
    chunks_a, pending = parser.parse_page(page_a, 3167)
    assert chunks_a == []  # row still open
    chunks_b, pending_b = parser.parse_page(page_b, 3168, pending)
    assert chunks_b == []  # merged row still open at end of page
    chunks_b, _ = parser.parse_page(FakePage([]), 3169, pending_b)
    assert len(chunks_b) == 1
    assert chunks_b[0]["hts"] == "9801.00.10"
    assert chunks_b[0]["general_raw"] == "Free"
    assert "advanced in value" in (chunks_b[0]["description"] or "")


def test_gn_split_only_top_level():
    """GN 3/4/11 split: (a)/(b) at x~65 split; nested (i) at x~83 does not."""
    parser = HTSParser()
    words = [
        W(62.0, 100.0, "General"),
        W(90.0, 100.0, "Note"),
        W(110.0, 100.0, "3"),
        W(64.8, 131.0, "(a)"),
        W(80.0, 131.0, "Rate"),
        W(82.8, 152.0, "(i)"),
        W(100.0, 152.0, "Except"),
        W(64.8, 180.0, "(b)"),
        W(80.0, 180.0, "Other"),
    ]
    chunks, _ = parser.parse_page(FakePage(words), 18)
    assert [c["id"] for c in chunks] == ["gn:3-p18", "gn:3-a", "gn:3-b"]
    assert "(i) Except" in chunks[1]["text"]  # nested marker stays inside (a)


def test_chapter99_stacking_plus_symbol():
    """Chapter 99 'applicable subheading + 200%' is cumulative stacking."""
    words = header_words() + footer_words("99 - III - 87", "XXII")
    words += [
        W(47.8, 449.9, "9903.85.67"),
        W(110.7, 449.9, "Aluminum"),
        W(380.7, 522.0, "The"),
        W(397.3, 522.0, "duty"),
        W(383.7, 532.2, "provided"),
        W(383.7, 542.4, "applicable"),
        W(383.7, 552.6, "subheading"),
        W(427.5, 552.6, "+"),
        W(383.7, 562.8, "200%"),
    ]
    parser = HTSParser()
    chunks, pending = parser.parse_page(FakePage(shuffled(words)), 3989)
    assert pending is not None
    chunks, _ = parser.parse_page(FakePage([]), 3990, pending)
    assert len(chunks) == 1
    assert chunks[0]["stacking"] == "cumulative"


def test_chapter99_subchapter_iv_additional_duties():
    """9904 pages: single 'Additional Duties' column -> general_raw."""
    words = [
        W(450.0, 75.0, "Additional"),
        W(482.0, 75.0, "Duties"),
        W(351.0, 66.0, "Unit"),
        W(45.7, 47.3, "XXII"),
        W(45.7, 55.7, "99"),
        W(55.4, 55.7, "-"),
        W(59.7, 55.7, "IV"),
        W(47.8, 174.3, "9904.02.01"),
        W(168.3, 174.3, "Valued"),
        W(380.7, 176.8, "66.6¢/kg"),
        W(47.8, 328.3, "9904.02.08"),
        W(168.3, 328.3, "Valued"),
        W(380.7, 330.7, "No"),
        W(393.6, 330.7, "additional"),
        W(431.5, 330.7, "duty"),
    ]
    parser = HTSParser()
    chunks1, pending = parser.parse_page(FakePage(shuffled(words)), 4013)
    assert pending is not None
    chunks2, _ = parser.parse_page(FakePage([]), 4014, pending)
    chunks = chunks1 + chunks2
    by_hts = {c["hts"]: c for c in chunks}
    assert by_hts["9904.02.01"]["kind"] == "chapter99"
    assert by_hts["9904.02.01"]["general_raw"] == "66.6¢/kg"
    assert by_hts["9904.02.01"]["stacking"] == "cumulative"
    assert by_hts["9904.02.08"]["general_raw"] == "No additional duty"
    assert by_hts["9904.02.08"]["stacking"] == "standalone"


def test_phantom_running_head_suppressed():
    """A continuation page re-printing an earlier 8-digit HTS (no rates)
    is a running head, not a duplicate row; following suffix rows still
    attach to it."""
    page_a = FakePage(
        shuffled(
            header_words()
            + footer_words("30-5", "VI")
            + [
                W(47.8, 179.5, "3004.90.92"),
                W(139.5, 179.5, "Other"),
                W(380.7, 179.7, "Free"),
                W(524.0, 179.7, "30%"),
                W(95.7, 189.9, "01"),
                W(153.9, 189.9, "For"),
                W(337.5, 190.1, "kg"),
            ]
        )
    )
    page_b = FakePage(
        shuffled(
            header_words()
            + footer_words("30-6", "VI")
            + [
                W(47.8, 93.7, "3004.90.92"),  # phantom re-print
                W(139.5, 93.7, "Other"),
                W(153.9, 103.9, "Other:"),
                W(95.7, 114.1, "02"),
                W(153.9, 114.1, "Containing"),
                W(337.5, 114.3, "kg"),
            ]
        )
    )
    parser = HTSParser()
    chunks_a, pending = parser.parse_page(page_a, 1589)
    # Page A flushed the 8-digit row when the suffix row started.
    assert [c["hts"] for c in chunks_a] == ["3004.90.92"]
    assert pending is not None and pending["hts"] == "3004.90.9201"
    chunks_b, pending_b = parser.parse_page(page_b, 1590, pending)
    # No duplicate 3004.90.92 chunk; the suffix row attaches to it.
    assert [c["hts"] for c in chunks_b] == ["3004.90.9201"]
    assert pending_b is not None and pending_b["hts"] == "3004.90.9202"
    chunks_c, _ = parser.parse_page(FakePage([]), 1591, pending_b)
    assert [c["hts"] for c in chunks_c] == ["3004.90.9202"]


def test_con_reprint_suppressed():
    """'(con.)' re-prints of a heading / 8-digit line are not new rows."""
    page_a = FakePage(
        shuffled(
            header_words()
            + footer_words("30-5", "VI")
            + [
                W(47.8, 100.0, "3004"),
                W(110.7, 100.0, "Medicaments"),
                W(47.8, 179.5, "3004.90.92"),
                W(139.5, 179.5, "Other"),
                W(380.7, 179.7, "Free"),
                W(524.0, 179.7, "30%"),
                W(95.7, 189.9, "01"),
                W(153.9, 189.9, "For"),
                W(337.5, 190.1, "kg"),
            ]
        )
    )
    page_b = FakePage(
        shuffled(
            header_words()
            + footer_words("30-6", "VI")
            + [
                W(47.8, 93.7, "3004"),
                W(68.8, 93.7, "(con.)"),
                W(110.7, 93.7, "Medicaments"),
                W(47.8, 165.1, "3004.90.92"),
                W(139.5, 165.1, "Other"),
                W(162.6, 165.1, "(con.)"),
                W(95.7, 205.9, "02"),
                W(153.9, 205.9, "Containing"),
                W(337.5, 206.1, "kg"),
            ]
        )
    )
    parser = HTSParser()
    chunks_a, pending = parser.parse_page(page_a, 1589)
    assert [c["hts"] for c in chunks_a] == ["3004", "3004.90.92"]
    chunks_b, pending_b = parser.parse_page(page_b, 1590, pending)
    # The (con.) re-prints create no rows; pending suffix row flushes.
    assert [c["hts"] for c in chunks_b] == ["3004.90.9201"]
    assert pending_b is not None and pending_b["hts"] == "3004.90.9202"
    chunks_c, _ = parser.parse_page(FakePage([]), 1591, pending_b)
    assert [c["hts"] for c in chunks_c] == ["3004.90.9202"]


def test_con_reprint_marker_on_next_line():
    """The 8-digit re-print's '(con.)' often sits on the FOLLOWING line
    (e.g. 6110.20.10 on p.2172): the no-marker re-print must still be
    suppressed via the first-row phantom check."""
    page_a = FakePage(
        shuffled(
            header_words()
            + footer_words("61-10", "XI")
            + [
                W(47.8, 100.0, "6110"),
                W(110.7, 100.0, "Sweaters"),
                W(47.8, 179.5, "6110.20.10"),
                W(139.5, 179.5, "Containing"),
                W(380.7, 179.7, "Free"),
                W(524.0, 179.7, "45%"),
                W(95.7, 189.9, "24"),
                W(153.9, 189.9, "Womens"),
                W(337.5, 190.1, "doz."),
            ]
        )
    )
    page_b = FakePage(
        shuffled(
            header_words()
            + footer_words("61-11", "XI")
            + [
                W(47.8, 93.7, "6110"),
                W(68.8, 93.7, "(con.)"),
                W(110.7, 93.7, "Sweaters"),
                W(47.8, 134.5, "6110.20.10"),
                W(139.5, 134.5, "Containing"),
                W(47.8, 144.7, "(con.)"),
                W(139.5, 144.7, "(con.)"),
                W(95.7, 175.3, "26"),
                W(153.9, 175.3, "Knit"),
                W(337.5, 175.5, "doz."),
            ]
        )
    )
    parser = HTSParser()
    chunks_a, pending = parser.parse_page(page_a, 2171)
    assert [c["hts"] for c in chunks_a] == ["6110", "6110.20.10"]
    chunks_b, pending_b = parser.parse_page(page_b, 2172, pending)
    assert [c["hts"] for c in chunks_b] == ["6110.20.1024"]
    assert pending_b is not None and pending_b["hts"] == "6110.20.1026"
    chunks_c, _ = parser.parse_page(FakePage([]), 2173, pending_b)
    assert [c["hts"] for c in chunks_c] == ["6110.20.1026"]


# ---------------------------------------------------------------------------
# Real-PDF tests (skip when the PDF is absent)
# ---------------------------------------------------------------------------

pdf_present = DEFAULT_PDF.exists()
needs_pdf = pytest.mark.skipif(not pdf_present, reason="HTS PDF not present")


@needs_pdf
def test_validate_known_lines_real_pdf():
    result = parse_pdf(DEFAULT_PDF, VALIDATE_PAGES)
    failures = [(hts, detail) for hts, ok, detail in validate_chunks(result["chunks"]) if not ok]
    assert failures == []
    # Every KNOWN_LINES entry was actually checked (not vacuous).
    assert len(validate_chunks(result["chunks"])) == len(KNOWN_LINES)


@needs_pdf
def test_sample_pages_real_pdf():
    result = parse_pdf(DEFAULT_PDF, SAMPLE_PAGES)
    chunks = result["chunks"]
    assert chunks
    kinds = {c["kind"] for c in chunks}
    assert "general_note" in kinds  # GN3 excerpts
    assert "chapter99" in kinds  # ch99 excerpts
    appendix = [c for c in chunks if c["kind"] == "appendix"]
    assert appendix and all(c["reference_only"] for c in appendix)
    assert all(c["revision"] == "2026 Rev 21" for c in chunks)


@needs_pdf
def test_known_line_8501_real_pdf():
    result = parse_pdf(DEFAULT_PDF, [2875])
    by_hts = {c["hts"]: c for c in result["chunks"]}
    c = by_hts["8501.51.20"]
    assert c["general_raw"] == "2.5%"
    assert (c["special_raw"] or "").startswith("Free")
    assert c["col2_raw"] == "35%"

def test_ch99_row_classified_by_hts_prefix_when_parsed_alone():
    """Regression: 9903.88.01 parsed on its page alone must still be
    kind chapter99 / stacking cumulative (was tariff_line/standalone
    because chapter came from page context). Row is at PDF p.3989;
    the operative U.S. Note 20 text is at p.3563 — different pages."""
    import os
    pdf = os.environ.get("HTS_PDF", os.path.expanduser("~/workspace/user/files/2026HTSRev21_0_cgh9.pdf"))
    if not os.path.exists(pdf):
        pytest.skip("HTS PDF not present")
    from rag.ingest.parse_hts_pdf import parse_pdf
    r = parse_pdf(pdf, pages=[3989])
    row = next((c for c in r["chunks"] if c.get("hts") == "9903.88.01"), None)
    assert row is not None
    assert row["kind"] == "chapter99"
    assert row["chapter"] == 99
    assert row["stacking"] == "cumulative"
    assert "plus 25%" in (row["general_raw"] or "")


def test_gn_numbering_advances_past_3():
    """Regression: GN pages headlined '4. Products of Countries ...' /
    '33. United States-Korea ...' (not 'General Note N') must not all be
    labelled gn:3-*. GN 4 content (GSP lapsed footnote, p.26) must be gn:4."""
    import os
    pdf = os.environ.get("HTS_PDF", os.path.expanduser("~/workspace/user/files/2026HTSRev21_0_cgh9.pdf"))
    if not os.path.exists(pdf):
        pytest.skip("HTS PDF not present")
    from rag.ingest.parse_hts_pdf import parse_pdf
    r = parse_pdf(pdf, pages=[18, 26, 670])
    ids = [c.get("id") or "" for c in r["chunks"]]
    assert any(i.startswith("gn:3") for i in ids), ids
    assert any(i.startswith("gn:4") for i in ids), ids
    assert any(i.startswith("gn:33") for i in ids), ids
    lapse = [c for c in r["chunks"] if "has not renewed" in (c.get("text") or "")]
    assert lapse and lapse[0].get("id", "").startswith("gn:4"), [c.get("id") for c in lapse]
