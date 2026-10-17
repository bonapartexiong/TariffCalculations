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


# --- Hierarchy regression tests (2026-10-17 retrieval fix) -----------------
# Synthetic chunks that replicate the real HTS hierarchy: leaf
# descriptions are fragments ("Valued over $20 each"), and the
# discriminating text lives in ancestors (group titles like
# "Handbags, whether or not...") carried in full_description.
# These guard the three root causes: (1) ancestor propagation,
# (2) score saturation / HTS tie-break, (3) synonym expansion.
HEADING_4202 = (
    "Trunks, suitcases, vanity cases, attache cases, briefcases, "
    "school satchels, spectacle cases, binocular cases, camera cases, "
    "musical instrument cases, gun cases, holsters and similar "
    "containers; traveling bags, insulated food or beverage bags, "
    "toiletry bags, knapsacks and backpacks, handbags, shopping bags, "
    "wallets, purses, map cases, cigarette cases, tobacco pouches, "
    "tool bags, sports bags, bottle cases, jewelry boxes, powder cases, "
    "cutlery cases and similar containers, of leather or of composition "
    "leather, of sheeting of plastics, of textile materials, of "
    "vulcanized fiber or of paperboard, or wholly or mainly covered "
    "with such materials or with paper"
)


@pytest.fixture()
def idx_hierarchy():
    chunks = [
        # Leather handbag: generic leaf under the handbag group.
        chunk(id="hts:4202.21.90", hts="4202.21.90", page=1780,
              description="Valued over $20 each",
              full_description=HEADING_4202 + " | Handbags, whether or not "
              "with shoulder strap, including those without handle: With "
              "outer surface of leather or of composition leather: | "
              "Valued over $20 each",
              text="4202.21.90 Valued over $20 each General: 9% Special: Free (A, AU, BH, CL, CO, D, IL, JO, KR, MA, OM, P, PA, PE, S, SG) Col2: 35%",
              general_raw="9%", dutiable=True),
        # Trunks with leather surface — must NOT win "leather handbag".
        chunk(id="hts:4202.11.00", hts="4202.11.00", page=1779,
              description="Trunks, suitcases, vanity cases, attache cases, briefcases, school satchels and similar containers: With outer surface of leather or of composition leather",
              full_description=HEADING_4202 + " | Trunks, suitcases, vanity "
              "cases, attache cases, briefcases, school satchels and "
              "similar containers: With outer surface of leather or of "
              "composition leather",
              text="4202.11.00 Trunks... General: 6.5%",
              general_raw="6.5%", dutiable=True),
        # Reptile-leather pocket articles — specific leaf, generic query.
        chunk(id="hts:4202.31.30", hts="4202.31.30", page=1782,
              description="Of reptile leather",
              full_description=HEADING_4202 + " | Articles of a kind normally "
              "carried in the pocket or in the handbag: With outer surface "
              "of leather or of composition leather: | Of reptile leather",
              text="4202.31.30 Of reptile leather General: 5.3%",
              general_raw="5.3%", dutiable=True),
        # Shoe polish — the old wrong answer for "leather handbag".
        chunk(id="hts:3405.10.00", hts="3405.10.00", page=1500,
              description="Polishes, creams and similar preparations for footwear or leather",
              full_description="Polishes, creams and similar preparations "
              "for footwear, furniture, floors, coachwork, glass or metal "
              "| Polishes, creams and similar preparations for footwear "
              "or leather",
              text="3405.10.00 shoe polish General: 2.9%",
              general_raw="2.9%", dutiable=True),
        # Plastic handbags — matches "handbag" only, not "leather".
        chunk(id="hts:3926.90.33", hts="3926.90.33", page=1600,
              description="Handbags",
              full_description="Other articles of plastics and articles of "
              "other materials of headings 3901 to 3914 | Other | Handbags",
              text="3926.90.33 Handbags General: 5.3%",
              general_raw="5.3%", dutiable=True),
        # Sneakers: sports-footwear leaf under the 6404.11 group.
        chunk(id="hts:6404.11.90", hts="6404.11.90", page=2413,
              description="Other",
              full_description="Footwear with outer soles of rubber, plastics, "
              "leather or composition leather and uppers of textile materials "
              "| Footwear with outer soles of rubber or plastics: Sports "
              "footwear; tennis shoes, basketball shoes, gym shoes, training "
              "shoes and the like: | Other",
              text="6404.11.90 Other General: 37.5%",
              general_raw="37.5%", dutiable=True),
        chunk(id="hts:6404.11", hts="6404.11", page=2413,
              description="Sports footwear; tennis shoes, basketball shoes, gym shoes, training shoes and the like:",
              full_description="Footwear with outer soles of rubber, plastics, "
              "leather or composition leather and uppers of textile materials "
              "| Footwear with outer soles of rubber or plastics: Sports "
              "footwear; tennis shoes, basketball shoes, gym shoes, training "
              "shoes and the like:",
              text="6404.11 Sports footwear",
              general_raw=None, dutiable=False),
        # Glass — the old wrong answer for "laptop computer".
        chunk(id="hts:7010.20.20", hts="7010.20.20", page=2600,
              description="Stoppers, lids and other closures",
              full_description="Carboys, bottles, flasks, jars, pots, phials, "
              "ampoules and other containers, of glass, of a kind used for "
              "the conveyance or packing of goods; preserving jars of glass; "
              "stoppers, lids and other closures, of glass | Stoppers, lids "
              "and other closures",
              text="7010.20.20 Stoppers General: 1.6%",
              general_raw="1.6%", dutiable=True),
        # Laptop: portable ADP machine.
        chunk(id="hts:8471.30.01", hts="8471.30.01", page=2832,
              description="Portable automatic data processing machines, weighing not more than 10 kg, consisting of at least a central processing unit, a keyboard and a display",
              full_description="Automatic data processing machines and units "
              "thereof; magnetic or optical readers, machines for "
              "transcribing data onto data media in coded form and machines "
              "for processing such data, not elsewhere specified or included "
              "| Portable automatic data processing machines, weighing not "
              "more than 10 kg, consisting of at least a central processing "
              "unit, a keyboard and a display",
              text="8471.30.01 Portable ADP General: Free",
              general_raw="Free", dutiable=True),
        # Coffee beans (not lima beans).
        chunk(id="hts:0901.11.00", hts="0901.11.00", page=1000,
              description="Coffee, not roasted: Not decaffeinated",
              full_description="Coffee, whether or not roasted or "
              "decaffeinated; coffee husks and skins; coffee substitutes "
              "containing coffee in any proportion | Coffee, not roasted: "
              "Not decaffeinated",
              text="0901.11.00 Coffee General: Free",
              general_raw="Free", dutiable=True),
        chunk(id="hts:0708.20.10", hts="0708.20.10", page=900,
              description="Lima beans",
              full_description="Leguminous vegetables, shelled or unshelled, "
              "fresh or chilled | Beans (Vigna spp., Phaseolus spp.): "
              "Lima beans",
              text="0708.20.10 Lima beans General: 4.9 cents/kg",
              general_raw="4.9 cents/kg", dutiable=True),
        # Wine (not wine lees).
        chunk(id="hts:2204.10.00", hts="2204.10.00", page=1100,
              description="Sparkling wine",
              full_description="Wine of fresh grapes, including fortified "
              "wines; grape must other than that of heading 2009 | "
              "Sparkling wine",
              text="2204.10.00 Sparkling wine General: 19.8 cents/liter",
              general_raw="19.8 cents/liter", dutiable=True),
        chunk(id="hts:2303.30.00", hts="2303.30.00", page=1150,
              description="Wine lees; argol",
              full_description="Residues and waste from the food industries; "
              "prepared animal feed | Brewing or distilling dregs and "
              "waste | Wine lees; argol",
              text="2303.30.00 Wine lees General: Free",
              general_raw="Free", dutiable=True),
        # Pharmaceuticals: Ch. 30 medicaments (not waste / placebos /
        # beauty preparations that merely exclude medicaments).
        chunk(id="hts:3004.90.92", hts="3004.90.92", page=1300,
              description="Other",
              full_description="Medicaments (excluding goods of heading 3002, "
              "3005 or 3006) consisting of mixed or unmixed products for "
              "therapeutic or prophylactic uses, put up in measured doses "
              "(including those in the form of transdermal administration "
              "systems) or in forms or packings for retail sale | Other",
              text="3004.90.92 Other General: Free",
              general_raw="Free", dutiable=True),
        chunk(id="hts:3006.92.00", hts="3006.92.00", page=1320,
              description="Waste pharmaceuticals",
              full_description="Pharmaceutical goods specified in note 4 to "
              "this chapter | Waste pharmaceuticals",
              text="3006.92.00 Waste pharmaceuticals General: Free",
              general_raw="Free", dutiable=True),
        chunk(id="hts:3006.93.80", hts="3006.93.80", page=1321,
              description="Placebos and clinical trial kits containing other chemicals other than medicaments",
              full_description="Pharmaceutical goods specified in note 4 to "
              "this chapter | Placebos and blinded (or double-blinded) "
              "clinical trial kits for a recognized clinical trial, put up "
              "in measured doses | Placebos and clinical trial kits "
              "containing other chemicals other than medicaments",
              text="3006.93.80 Placebos General: Free",
              general_raw="Free", dutiable=True),
        chunk(id="hts:3304.30.00", hts="3304.30.00", page=1350,
              description="Manicure or pedicure preparations",
              full_description="Beauty or make-up preparations and "
              "preparations for the care of the skin (other than "
              "medicaments), including sunscreen or sun tan preparations; "
              "manicure or pedicure preparations | Manicure or pedicure "
              "preparations",
              text="3304.30.00 Manicure preparations General: Free",
              general_raw="Free", dutiable=True),
        # Steel screws (not stainless-steel waste/scrap).
        chunk(id="hts:7318.15.50", hts="7318.15.50", page=2624,
              description="Other",
              full_description="Screws, bolts, nuts, coach screws, screw "
              "hooks, rivets, cotters, cotter pins, washers (including "
              "spring washers) and similar articles, of iron or steel | "
              "Threaded articles: Other screws and bolts, whether or not "
              "with their nuts or washers: | Other",
              text="7318.15.50 Other General: 8.5%",
              general_raw="8.5%", dutiable=True),
        chunk(id="hts:7204.21.00", hts="7204.21.00", page=2500,
              description="Waste and scrap of stainless steel",
              full_description="Ferrous waste and scrap; remelting scrap "
              "ingots of iron or steel | Waste and scrap of alloy steel: "
              "Of stainless steel | Waste and scrap of stainless steel",
              text="7204.21.00 Waste and scrap General: Free",
              general_raw="Free", dutiable=True),
    ]
    return Index(chunks)


def test_leather_handbag_hierarchy(idx_hierarchy):
    hits = idx_hierarchy.search("leather handbag", dutiable_only=True)
    assert hits and hits[0]["chunk"]["hts"] == "4202.21.90"
    by_hts = {h["chunk"]["hts"]: h["score"] for h in hits}
    # Trunks, shoe polish, plastic handbags, reptile pocket articles
    # must all rank below the generic leather-handbag leaf.
    assert by_hts["4202.21.90"] > by_hts["4202.11.00"]
    assert by_hts["4202.21.90"] > by_hts["3405.10.00"]
    assert by_hts["4202.21.90"] > by_hts["3926.90.33"]
    assert by_hts["4202.21.90"] > by_hts["4202.31.30"]


def test_sneakers_hierarchy(idx_hierarchy):
    for q in ("sneakers", "athletic footwear"):
        hits = idx_hierarchy.search(q, dutiable_only=True)
        assert hits and hits[0]["chunk"]["hts"] == "6404.11.90", q


def test_laptop_hierarchy(idx_hierarchy):
    hits = idx_hierarchy.search("laptop computer", dutiable_only=True)
    assert hits and hits[0]["chunk"]["hts"] == "8471.30.01"
    # Glass stoppers share no query token — zero evidence, not merely
    # ranked below.
    assert all(h["chunk"]["hts"] != "7010.20.20" for h in hits)


def test_coffee_beans_hierarchy(idx_hierarchy):
    hits = idx_hierarchy.search("coffee beans", dutiable_only=True)
    assert hits and hits[0]["chunk"]["hts"] == "0901.11.00"
    by_hts = {h["chunk"]["hts"]: h["score"] for h in hits}
    assert by_hts["0901.11.00"] > by_hts["0708.20.10"]


def test_wine_hierarchy(idx_hierarchy):
    hits = idx_hierarchy.search("wine", dutiable_only=True)
    assert hits and hits[0]["chunk"]["hts"] == "2204.10.00"
    by_hts = {h["chunk"]["hts"]: h["score"] for h in hits}
    assert by_hts["2204.10.00"] > by_hts["2303.30.00"]


def test_pharmaceuticals_hierarchy(idx_hierarchy):
    hits = idx_hierarchy.search("pharmaceuticals", dutiable_only=True)
    assert hits and hits[0]["chunk"]["hts"] == "3004.90.92"
    by_hts = {h["chunk"]["hts"]: h["score"] for h in hits}
    # "pharmaceuticals" expands (replacement) to "medicament", so the
    # waste line — whose text says "pharmaceutical", never
    # "medicament" — has zero evidence and is not returned at all.
    assert "3006.92.00" not in by_hts
    # Placebos (explicitly "other than medicaments") and the beauty
    # heading that merely excludes medicaments are either excluded
    # (rank <= 0) or rank below the medicaments line.
    for excluded in ("3006.93.80", "3304.30.00"):
        assert excluded not in by_hts or by_hts["3004.90.92"] > by_hts[excluded]


def test_pharmaceutical_waste_still_finds_waste(idx_hierarchy):
    # Replacement is exact-query-only: "pharmaceutical waste" keeps
    # its base tokens and must still find the waste line.
    hits = idx_hierarchy.search("pharmaceutical waste", dutiable_only=True)
    assert hits and hits[0]["chunk"]["hts"] == "3006.92.00"


def test_steel_screws_hierarchy(idx_hierarchy):
    hits = idx_hierarchy.search("steel screws", dutiable_only=True)
    assert hits and hits[0]["chunk"]["hts"] == "7318.15.50"
    by_hts = {h["chunk"]["hts"]: h["score"] for h in hits}
    assert by_hts["7318.15.50"] > by_hts["7204.21.00"]


def test_score_saturation_no_tie(idx_hierarchy):
    # Two chunks with very different raw scores must NOT tie: the old
    # per-chunk normalisation clamped both to 1.0 and let the HTS
    # tie-break pick the wrong line.
    hits = idx_hierarchy.search("leather handbag", dutiable_only=True)
    assert len(hits) >= 2
    assert hits[0]["score"] == 1.0
    assert hits[1]["score"] < 1.0
    assert hits[0]["score"] > hits[1]["score"]


def test_rate_boilerplate_excluded(idx_hierarchy):
    # Tariff chunks index description + full_description only. A chunk
    # whose *text* (rate boilerplate) contains "Free"/"General" must
    # NOT match a query for those rate words.
    hits = idx_hierarchy.search("free")
    assert all(h["chunk"]["hts"] != "4202.21.90" for h in hits)
    hits = idx_hierarchy.search("general")
    assert all(h["chunk"]["hts"] != "4202.21.90" for h in hits)


def test_dutiable_only_filters_headings(idx_hierarchy):
    hits = idx_hierarchy.search("sneakers", dutiable_only=True)
    assert all(h["chunk"].get("dutiable") for h in hits)
    assert all(h["chunk"]["hts"] != "6404.11" for h in hits)
