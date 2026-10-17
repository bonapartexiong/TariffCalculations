"""Answer tests — synthetic chunks only, no real PDF."""
from rag.answer import answer_question
from rag.retrieval import Index


def chunk(**kw):
    base = {
        "id": "x", "kind": "tariff_line", "text": "", "hts": None,
        "description": "", "unit": None, "general_raw": None,
        "special_raw": None, "col2_raw": None, "special_programs": [],
        "footnotes": [], "page": 1, "chapter": None, "section": None,
        "revision": "2026 Rev 21", "publication": "Pub. 5802",
        "effective_period": None, "stacking": "unknown",
        "reference_only": False, "dutiable": True,
    }
    base.update(kw)
    return base


def sneaker_index():
    return Index([
        chunk(id="hts:6404.11.00", hts="6404.11.00", page=100,
              description="Sports footwear; tennis shoes, basketball shoes, gym shoes, training shoes and the like",
              text="Sports footwear; tennis shoes, basketball shoes. Athletic footwear with outer soles of rubber.",
              general_raw="20%", special_raw="Free (A)",
              special_programs=[{"symbol": "A", "rate_raw": "Free"}],
              dutiable=True),
    ])


def test_answer_cites_page():
    res = answer_question("sneakers duty", sneaker_index())
    assert res["abstained"] is False
    assert "(6404.11.00, p.100, Rev 21)" in res["answer"]
    assert res["citations"][0]["hts"] == "6404.11.00"
    assert res["citations"][0]["page"] == 100


def test_answer_abstains_on_nonsense():
    res = answer_question("asdfgh qwerty zzz", sneaker_index())
    assert res["abstained"] is True
    assert res["answer"].startswith("Not found in HTS 2026 Rev 21")


def test_answer_flags_gsp():
    res = answer_question("sneakers duty", sneaker_index())
    assert "GSP lapsed" in res["answer"]
    assert "Free (A) not currently claimable" in res["answer"]


def test_answer_flags_gn12_and_ch99():
    idx = Index([
        chunk(id="gn:12", kind="general_note", hts=None, page=26,
              description="", text="General Note 12 was deleted.",
              dutiable=False),
        chunk(id="hts:9903.88.01", kind="chapter99", hts="9903.88.01",
              page=900, description="Additional duties",
              text="Chapter 99 additional 25 percent duties.",
              general_raw="25%", effective_period="2025-01-01 to 2026-12-31",
              dutiable=True),
    ])
    res = answer_question("chapter 99 additional duties GN 12", idx)
    assert "GN 12 deleted" in res["answer"]
    assert "effective_period/expiry" in res["answer"]


def test_answer_flags_mpf_external():
    idx = Index([
        chunk(id="gn:mpf", kind="general_note", hts=None, page=30,
              description="",
              text="The Merchandise Processing Fee (MPF) is collected by CBP.",
              dutiable=False),
    ])
    res = answer_question("merchandise processing fee MPF", idx)
    assert "MPF/HMF not in HTS PDF, CBP external" in res["answer"]


def test_llm_bad_output_falls_back_to_draft():
    res = answer_question("sneakers duty", sneaker_index(),
                          llm=lambda prompt: "garbage with no citations")
    assert "(6404.11.00, p.100, Rev 21)" in res["answer"]


def test_llm_good_output_used():
    good = "Rephrased: sports footwear at 20% (6404.11.00, p.100, Rev 21)."
    res = answer_question("sneakers duty", sneaker_index(),
                          llm=lambda prompt: good)
    assert res["answer"] == good


def test_gsp_question_flags_lapsed_even_without_gsp_lines():
    """Regression (demo): 'Is Free (A) claimable under GSP?' must flag
    GSP-lapsed even when BM25 retrieves only Free tariff lines."""
    from rag.answer import answer_question
    from rag.retrieval import Index
    chunks = [
        {"id": "hts:4907.00.00", "kind": "tariff_line", "text": "stamps Free",
         "hts": "4907.00.00", "description": "stamps", "general_raw": "Free",
         "special_raw": "", "col2_raw": "66¢/kg", "page": 1897,
         "revision": "2026 Rev 21", "special_programs": []},
        {"id": "gn:4", "kind": "general_note",
         "text": "General Note 4 Generalized System of Preferences GSP. "
                 "Congress has not renewed the effectiveness of the GSP program.",
         "hts": None, "description": None, "general_raw": None,
         "special_raw": None, "col2_raw": None, "page": 26,
         "revision": "2026 Rev 21", "special_programs": []},
    ]
    idx = Index(chunks)
    out = answer_question("Is Free (A) claimable under GSP right now?", idx)
    assert out["abstained"] is False
    assert "GSP lapsed" in out["answer"]


def test_gsp_flag_survives_newline_wrapped_phrase():
    """Regression: PDF text wraps 'Generalized\\nSystem of Preferences'
    across a line break; substring flags must normalize whitespace."""
    from rag.answer import answer_question
    from rag.retrieval import Index
    chunks = [
        {"id": "gn:4-a", "kind": "general_note",
         "text": "beneficiary developing countries for the purposes of the Generalized\nSystem of Preferences. Congress has not renewed the effectiveness.",
         "hts": None, "description": None, "general_raw": None,
         "special_raw": None, "col2_raw": None, "page": 26,
         "revision": "2026 Rev 21", "special_programs": []},
        {"id": "hts:4907.00.00", "kind": "tariff_line", "text": "stamps Free",
         "hts": "4907.00.00", "description": "stamps", "general_raw": "Free",
         "special_raw": "", "col2_raw": "66¢/kg", "page": 1897,
         "revision": "2026 Rev 21", "special_programs": []},
    ]
    idx = Index(chunks)
    out = answer_question("Is Free (A) claimable under GSP right now?", idx)
    assert "GSP lapsed" in out["answer"]
    assert any(c.get("id") == "gn:4-a" for c in out["chunks"])
