"""Retrieval tests — synthetic chunks only, no real PDF."""
import pytest

from rag.retrieval import Index, normalize_hTS, tokenize


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


@pytest.fixture()
def idx():
    chunks = [
        chunk(id="hts:6404.11.00", hts="6404.11.00", page=100,
              description="Sports footwear; tennis shoes, basketball shoes, gym shoes, training shoes and the like",
              text="Chapter 64 Footwear. Sports footwear; tennis shoes, basketball shoes, gym shoes, training shoes and the like. Athletic footwear with outer soles of rubber.",
              general_raw="20%", dutiable=True),
        chunk(id="hts:6404.99.00", hts="6404.99.00", page=100,
              description="Other",
              text="Other footwear with outer soles of rubber or plastics.",
              general_raw="20%", dutiable=True),
        chunk(id="hts:3004.90.00", hts="3004.90.00", page=200,
              description="Medicaments, put up in measured doses or in forms for retail sale",
              text="Medicaments, put up in measured doses or in forms or packings for retail sale.",
              general_raw="Free", dutiable=True),
        chunk(id="hts:3825.10.00", hts="3825.10.00", page=210,
              description="Pharmaceutical waste",
              text="Pharmaceutical waste, municipal waste, sewage sludge.",
              general_raw="Free", dutiable=True),
        chunk(id="hts:2204.21.00", hts="2204.21.00", page=50,
              description="Wine of fresh grapes, in bottles",
              text="Wine of fresh grapes, including fortified wines; grape must. 2204. Wine in bottles.",
              general_raw="6.3 cents/liter", dutiable=True),
        chunk(id="hts:2303.30.00", hts="2303.30.00", page=60,
              description="Wine lees; argol",
              text="Wine lees; argol. Residues and waste from the food industries.",
              general_raw="Free", dutiable=True),
        chunk(id="hts:0404.10.00", hts="0404.10.00", page=10,
              description="Whey, whether or not concentrated or containing added sugar",
              text="Whey, whether or not concentrated or containing added sugar or other sweetening matter.",
              general_raw="Free", dutiable=True),
        chunk(id="hts:4202.11.00", hts="4202.11.00", page=300,
              description="Trunks, suitcases, vanity cases and similar containers, with outer surface of leather",
              text="Trunks, suitcases, vanity cases, executive cases, briefcases, school satchels and similar containers. 4202.11.00 trunks with outer surface of leather.",
              general_raw="8%", dutiable=True),
        chunk(id="note:ch30", kind="chapter_note", hts=None, page=195,
              description="", text="Chapter 30 note about medicaments and pharmaceuticals.",
              dutiable=False),
    ]
    return Index(chunks)


def test_10_digit_truncation(idx):
    for code in ("4202110010", "4202.11.00.10", "4202.11.00", "4202 11 00"):
        c, warning = idx.lookup_hts(code)
        assert c is not None and c["hts"] == "4202.11.00", code
        assert warning is None


def test_heading_warning():
    heading_only = Index([chunk(id="hts:4202.11", hts="4202.11", page=300,
                                description="Trunks, suitcases and similar containers",
                                text="Heading 4202 trunks, suitcases and similar containers.",
                                dutiable=False)])
    c, warning = heading_only.lookup_hts("4202.11")
    assert c is not None and c["hts"] == "4202.11"
    assert warning is not None and "heading" in warning.lower()


def test_empty_hts_never_returned(idx):
    c, _ = idx.lookup_hts("4202.11.00")
    assert c is not None and c["hts"] == "4202.11.00"
    c, warning = idx.lookup_hts("9999.99.99")
    assert c is None
    assert warning is not None


def test_normalize_hts():
    assert normalize_hTS("4202.11") == "4202.11"
    assert normalize_hTS("4202.11.00") == "4202.11.00"
    assert normalize_hTS("4202.11.00.10") == "4202.11.00"
    assert normalize_hTS("4202110010") == "4202.11.00"


def test_tokenize_no_other():
    assert tokenize("no other") == ["no_other"]
    assert "other" not in tokenize("no other footwear")


def test_pharmaceuticals_not_waste(idx):
    hits = idx.search("pharmaceuticals")
    assert hits and hits[0]["chunk"]["hts"] == "3004.90.00"
    waste = [h for h in hits if h["chunk"]["hts"] == "3825.10.00"]
    if waste:
        assert waste[0]["score"] < hits[0]["score"]


def test_wine_not_lees(idx):
    hits = idx.search("wine")
    assert hits and hits[0]["chunk"]["hts"] == "2204.21.00"
    lees = [h for h in hits if h["chunk"]["hts"] == "2303.30.00"]
    if lees:
        assert lees[0]["score"] < hits[0]["score"]


def test_sneakers_not_waste_lees_404(idx):
    hits = idx.search("sneakers")
    assert hits and hits[0]["chunk"]["hts"] == "6404.11.00"
    returned = {h["chunk"]["hts"] for h in hits}
    assert "3825.10.00" not in returned  # pharmaceutical waste
    assert "2303.30.00" not in returned  # wine lees
    assert "0404.10.00" not in returned  # 0404 whey ("404") line


def test_other_rows_deprioritized(idx):
    hits = idx.search("footwear")
    by_hts = {h["chunk"]["hts"]: h["score"] for h in hits}
    assert by_hts["6404.11.00"] > by_hts["6404.99.00"]


def test_score_clamp(idx):
    for q in ("sneakers", "wine", "pharmaceuticals", "footwear", "trunks"):
        for h in idx.search(q):
            assert 0.0 <= h["score"] <= 1.0


def test_nonsense_empty(idx):
    assert idx.search("asdfgh qwerty zzz") == []
    assert idx.search("blorp glorp") == []


def test_save_load_roundtrip(idx, tmp_path):
    path = str(tmp_path / "index.jsonl")
    idx.save(path)
    idx2 = Index().load(path)
    assert len(idx2.chunks) == len(idx.chunks)
    hits = idx2.search("sneakers")
    assert hits and hits[0]["chunk"]["hts"] == "6404.11.00"


def test_engine_reports_bm25(idx):
    assert idx.engine in ("bm25", "bm25+embedding")
