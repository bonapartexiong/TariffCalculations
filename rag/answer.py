"""Grounded answer composition (Module B).

answer_question() retrieves, then composes a structured answer from the
retrieved chunks ONLY. An optional LLM callable may ONLY rephrase the
grounded draft; its output is validated for citations and the draft is
returned if validation fails. Never invents a rate.
"""
import re

REVISION = "2026 Rev 21"
ABSTAIN_TEXT = (
    "Not found in HTS 2026 Rev 21. No reliable classification or rate "
    "could be retrieved for this question."
)
GRI1_SCAFFOLD = (
    "Under GRI 1, classification is determined by terms of headings + "
    "section/chapter notes. The candidates below are ranked by retrieval; "
    "confirm the full 8-digit dutiable line and any applicable notes "
    "before relying on a rate."
)
_CITATION_RE = re.compile(r"\([^()]*,\s*p\.\d+[^()]*Rev 21\)")


def _citation(chunk):
    hts = chunk.get("hts") or "—"
    page = chunk.get("page")
    page_s = "p.%s" % page if page is not None else "p.?"
    return "(%s, %s, Rev 21)" % (hts, page_s,)


def _citation_dict(chunk):
    return {
        "hts": chunk.get("hts"),
        "page": chunk.get("page"),
        "revision": chunk.get("revision") or REVISION,
        "general_raw": chunk.get("general_raw"),
    }


def _detect_flags(chunks):
    flags = []
    # Normalize whitespace: PDF text wraps phrases across line breaks
    # ("Generalized\nSystem of Preferences"), which breaks substring flags.
    combined = " ".join(
        " ".join([
            str(c.get("text") or ""),
            str(c.get("description") or ""),
            str(c.get("general_raw") or ""),
            str(c.get("special_raw") or ""),
            str(c.get("col2_raw") or ""),
            str(c.get("id") or ""),
        ])
        for c in chunks
    )
    combined = " ".join(combined.split())
    has_a = any(
        (p.get("symbol") or "").strip().upper() == "A"
        for c in chunks for p in (c.get("special_programs") or [])
    )
    if has_a or "GSP" in combined or "Free (A)" in combined or \
            "Generalized System of Preferences" in combined:
        flags.append(
            "GSP lapsed — Free (A) not currently claimable without flag "
            "(GN 4 fn p.26)")
    if ("GN 12" in combined or "General Note 12" in combined
            or any(str(c.get("id") or "") == "gn:12" for c in chunks)):
        flags.append("GN 12 deleted")
    if ("MPF" in combined or "Merchandise Processing" in combined
            or "HMF" in combined or "Harbor Maintenance" in combined):
        flags.append("MPF/HMF not in HTS PDF, CBP external")
    for c in chunks:
        hts = str(c.get("hts") or "")
        if c.get("kind") == "chapter99" or hts.startswith("99"):
            period = c.get("effective_period")
            if period:
                flags.append(
                    "Chapter 99 line %s — check effective_period/expiry: %s"
                    % (hts or "?", period))
            else:
                flags.append(
                    "Chapter 99 line %s — check effective_period/expiry; "
                    "rates may be time-limited." % (hts or "?",))
            break
    # Dedupe, preserving order.
    seen = set()
    out = []
    for f in flags:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def answer_question(question, index, calculator=None, llm=None):
    """Return {answer, citations, chunks, abstained, warnings}."""
    warnings = []
    if not question or not str(question).strip():
        return {"answer": ABSTAIN_TEXT, "citations": [], "chunks": [],
                "abstained": True, "warnings": warnings}
    hits = []
    if index is not None:
        try:
            hits = index.search(question, top_k=8)
        except Exception as exc:  # retrieval failure -> abstain, never invent
            warnings.append("Retrieval failed: %s" % exc)
            hits = []
    if not hits:
        return {"answer": ABSTAIN_TEXT, "citations": [], "chunks": [],
                "abstained": True, "warnings": warnings}

    chunks = [h["chunk"] for h in hits]
    for h in hits:
        if h.get("warning"):
            warnings.append(h["warning"])

    # Keyword-triggered General Note injection. BM25 alone often misses
    # the controlling note for program questions (e.g. "Is Free (A)
    # claimable?" retrieves Free tariff lines, not GN 4 — so the
    # GSP-lapsed flag never fired). Explicitly fetch the note chunk.
    _NOTE_QUERIES = [
        (("gsp", "generalized system of preferences", "free (a)", "(a)"),
         "General Note 4 Generalized System of Preferences GSP beneficiary"),
        (("usmca", '"s)"', "us-mexico-canada"), "General Note 11 USMCA"),
        (("korea", "kr)"), "General Note 33 Korea KORUS"),
        (("japan", "jp)"), "General Note 36 Japan trade agreement"),
        (("column 2", "cuba", "russia", "belarus", "north korea"),
         "General Note 3 Rates of Duty Column 2"),
        (("section 301", "301 "), "Chapter 99 note 20 Section 301 China additional duty"),
        (("section 232", "232 "), "Chapter 99 steel aluminum 9903.81 9903.85 additional duty"),
    ]
    ql = " " + str(question).lower() + " "
    for triggers, note_query in _NOTE_QUERIES:
        if any(t in ql for t in triggers):
            # BM25 drowns short note chunks under thousands of tariff
            # lines, so scan the index's own chunk list for the note text
            # directly (deterministic); fall back to search if unavailable.
            _needles = {
                "General Note 4 Generalized System of Preferences GSP beneficiary":
                    ("has not renewed", "generalized system of preferences"),
                "General Note 11 USMCA":
                    ("us-mexico-canada", "united states-mexico-canada"),
                "General Note 33 Korea KORUS": ("united states-korea",),
                "General Note 36 Japan trade agreement":
                    ("trade agreement between the united states and japan",),
                "General Note 3 Rates of Duty Column 2": ("column 2",),
                "Chapter 99 note 20 Section 301 China additional duty":
                    ("9903.88.01", "additional 25 percent"),
                "Chapter 99 steel aluminum 9903.81 9903.85 additional duty":
                    ("9903.81", "9903.85"),
            }.get(note_query, ())
            _found = None
            try:
                for _c in (getattr(index, "chunks", None) or []):
                    _t = " ".join(str(_c.get("text") or "").split()).lower()
                    if _c.get("kind") in ("general_note", "chapter_note",
                                          "section_note", "gri") and _needles \
                            and all(n in _t for n in _needles):
                        _found = _c
                        break
            except Exception:
                _found = None
            if _found is not None and all(
                    (_found.get("id") != c.get("id")) for c in chunks):
                chunks.append(_found)
            else:
                try:
                    for nh in index.search(note_query, top_k=20):
                        nc = nh["chunk"]
                        if nc.get("kind") in ("general_note", "chapter_note",
                                              "section_note", "gri") and all(
                            (nc.get("id") != c.get("id")) for c in chunks):
                            chunks.append(nc)
                            break
                except Exception:
                    pass

    # ---------------------------------------------------------- grounded draft
    lines = [GRI1_SCAFFOLD, "", "Classification candidates:"]
    for chunk in chunks:
        hts = chunk.get("hts") or "—"
        # Show the hierarchical description (ancestors + leaf), not the
        # bare leaf fragment ("Valued over $20 each" alone means nothing).
        desc = (
            chunk.get("full_description")
            or chunk.get("description")
            or (chunk.get("text") or "")[:200]
        )
        general = chunk.get("general_raw") or "—"
        special = chunk.get("special_raw") or "—"
        col2 = chunk.get("col2_raw") or "—"
        lines.append(
            "- %s — %s — General: %s; Special: %s; Col 2: %s %s"
            % (hts, desc, general, special, col2, _citation(chunk))
        )
    note_chunks = [c for c in chunks if (c.get("kind") or "") in (
        "chapter_note", "section_note", "general_note", "gri",
        "statistical_note")]
    lines.append("")
    lines.append("Notes affecting scope:")
    if note_chunks:
        for c in note_chunks:
            snippet = (c.get("text") or "")[:300].replace("\n", " ")
            lines.append("- %s: %s %s" % (c.get("id") or c.get("kind"),
                                          snippet, _citation(c)))
    else:
        lines.append("- None identified in the retrieved chunks.")
    flags = _detect_flags(chunks)
    if flags:
        lines.append("")
        lines.append("Flags:")
        for f in flags:
            lines.append("- %s" % f)
    lines.append("")
    lines.append(
        "Disclaimer: this is a retrieval summary from HTS 2026 Rev 21, "
        "not legal advice. Confirm the dutiable line, notes, and any "
        "Chapter 99 effective period before use.")
    draft = "\n".join(lines)

    citations = [_citation_dict(c) for c in chunks]
    answer = draft

    # ------------------------------------------------- optional LLM rephrase
    if llm is not None:
        try:
            prompt = (
                "Rephrase ONLY the grounded draft below. Do not introduce "
                "any new rates, HTS codes, facts, or claims. Every rate "
                "claim must keep its citation in the exact form "
                "(HTS, p.N, Rev 21). Preserve all flags verbatim. If you "
                "cannot rephrase without adding information, return the "
                "draft unchanged.\n\nGrounded draft:\n" + draft
            )
            out = llm(prompt)
            if isinstance(out, dict):
                out_text = out.get("answer") or out.get("text") or ""
            else:
                out_text = str(out or "")
            if out_text and "Rev 21" in out_text and _CITATION_RE.search(out_text):
                answer = out_text
            else:
                warnings.append(
                    "LLM rephrase failed citation validation; returning "
                    "grounded draft.")
        except Exception as exc:
            warnings.append(
                "LLM rephrase failed (%s); returning grounded draft." % exc)

    return {"answer": answer, "citations": citations, "chunks": chunks,
            "abstained": False, "warnings": warnings}
