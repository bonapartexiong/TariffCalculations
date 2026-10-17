"""Calculator tests — synthetic rates only, Decimal money, no real PDF."""
from decimal import Decimal

from rag.calculator import calculate


def rate(kind, raw, components):
    return {"kind": kind, "raw": raw, "components": components,
            "requires_quantity": kind in ("specific", "compound"),
            "note": "", "cross_ref_target": None}


def ad(pct):
    return rate("ad_valorem", "%s%%" % pct,
                [{"value": str(pct), "unit": "percent", "per": ""}])


def base_req(**kw):
    req = {"value": "1000", "quantity": None, "quantity_unit": None,
           "origin_country": "Mexico", "program": None, "mode": "air",
           "quota_status": "unknown", "chapter99_stack": [],
           "de_minimis": False}
    req.update(kw)
    return req


def test_mpf_min():
    r = calculate(base_req(value="100", rate=rate("free", "Free", [])))
    assert r["computable"] is True
    assert r["breakdown"]["mpf"] == Decimal("32.71")
    assert r["breakdown"]["base_duty"] == Decimal("0")


def test_mpf_cap():
    r = calculate(base_req(value="500000", rate=rate("free", "Free", [])))
    assert r["breakdown"]["mpf"] == Decimal("575")


def test_hmf_ocean_only():
    ocean = calculate(base_req(value="100000", mode="ocean",
                               rate=rate("free", "Free", [])))
    assert ocean["breakdown"]["hmf"] == Decimal("125")
    air = calculate(base_req(value="100000", mode="air",
                             rate=rate("free", "Free", [])))
    assert air["breakdown"]["hmf"] == Decimal("0")


def test_compound_9105_99_10():
    # 9105.99.10-style compound: 2% ad valorem + $0.50 per jewel.
    comp = rate("compound", "2% + 50 cents per jewel", [
        {"value": "2", "unit": "percent", "per": ""},
        {"value": "0.50", "unit": "jewel", "per": "jewel"},
    ])
    r = calculate(base_req(value="1000", quantity="10",
                           quantity_unit="each", jewel_count="5", rate=comp))
    assert r["computable"] is True
    # 1000 * 2% = 20.00 ; 5 jewels * 0.50 = 2.50 ; total 22.50
    assert r["breakdown"]["base_duty"] == Decimal("22.50")


def test_stacking_base_plus_ch99():
    r = calculate(base_req(value="1000", rate=ad("2.5"),
                           chapter99_stack=[{"hts": "9903.88.01",
                                             "rate": ad("25")}]))
    assert r["breakdown"]["base_duty"] == Decimal("25")
    assert len(r["breakdown"]["ch99_duties"]) == 1
    assert r["breakdown"]["ch99_duties"][0]["duty"] == Decimal("250")
    assert (r["breakdown"]["base_duty"]
            + r["breakdown"]["ch99_duties"][0]["duty"]) == Decimal("275")


def test_rounding_components_sum_to_subtotal():
    r = calculate(base_req(value="123.45", rate=ad("6.5")))
    b = r["breakdown"]
    # 123.45 * 6.5% = 8.02425 -> 8.02 (HALF_UP); MPF min 32.71; HMF 0 air.
    assert b["base_duty"] == Decimal("8.02")
    assert b["subtotal"] == b["base_duty"] + b["mpf"] + b["hmf"] + sum(
        d["duty"] for d in b["ch99_duties"])
    assert b["subtotal"] == b["total"]


def test_section_321_zero():
    r = calculate(base_req(value="500", de_minimis=True,
                           origin_country="Mexico", rate=ad("10")))
    assert r["computable"] is True
    assert r["breakdown"]["total"] == Decimal("0")
    assert r["breakdown"]["mpf"] == Decimal("0")
    assert r["breakdown"]["hmf"] == Decimal("0")


def test_section_321_china_suspended():
    r = calculate(base_req(value="500", de_minimis=True,
                           origin_country="China", rate=ad("10")))
    assert r["breakdown"]["base_duty"] == Decimal("50")
    assert any("suspended" in w.lower() for w in r["warnings"])


def test_special_lowest_auto():
    chunk = {"hts": "8708.99.00", "page": 1, "general_raw": "5%",
             "special_programs": [
                 {"symbol": "A", "rate": ad("3")},
                 {"symbol": "E", "rate": ad("2")},
             ]}
    r = calculate(base_req(value="1000", program="auto-lowest",
                           rate=ad("5"), chunk=chunk))
    assert r["breakdown"]["base_duty"] == Decimal("20")
    assert r["selected_program"] == "E"


def test_col2_cuba():
    chunk = {"hts": "0901.11.00", "page": 1, "general_raw": "5%",
             "col2_raw": "35%"}
    r = calculate(base_req(value="1000", origin_country="Cuba",
                           rate=ad("5"), col2_rate=ad("35"), chunk=chunk))
    assert r["breakdown"]["base_duty"] == Decimal("350")
    assert any("Column 2" in w for w in r["warnings"])


def test_specific_requires_quantity():
    spec = rate("specific", "$2 per kg",
                [{"value": "2", "unit": "kg", "per": "kg"}])
    r = calculate(base_req(value="100", rate=spec))
    assert r["computable"] is False
    assert "quantity" in (r.get("explanation") or "").lower()


def test_in_lieu_no_base():
    r = calculate(base_req(value="1000", rate=ad("2.5"),
                           chapter99_stack=[{"hts": "9903.88.01",
                                             "rate": ad("25"),
                                             "stacking": "in_lieu"}]))
    assert r["breakdown"]["base_duty"] == Decimal("0")
    assert r["breakdown"]["ch99_duties"][0]["duty"] == Decimal("250")


def test_mpf_exempt_program_actually_claimed():
    chunk = {"hts": "8708.99.00", "page": 1, "general_raw": "5%",
             "special_programs": [{"symbol": "AU", "rate": ad("0")}]}
    r = calculate(base_req(value="1000", program="AU",
                           rate=ad("5"), chunk=chunk))
    assert r["breakdown"]["mpf"] == Decimal("0")


def test_quota_out_warning():
    r = calculate(base_req(value="1000", rate=ad("5"),
                           quota_status="out"))
    assert any("Over-quota" in w for w in r["warnings"])


def test_provenance_and_citations():
    chunk = {"hts": "4202.11.00", "page": 300, "general_raw": "8%",
             "revision": "2026 Rev 21"}
    r = calculate(base_req(value="100", rate=ad("8"), chunk=chunk))
    assert r["provenance"]["revision"] == "2026 Rev 21"
    assert "external" in r["provenance"]["source"]
    assert r["citations"][0]["hts"] == "4202.11.00"
    assert r["citations"][0]["page"] == 300


def test_noncomputable_breakdown_is_none_not_zero():
    """Regression: a refused calculation must not emit $0.00 components
    (a $0 breakdown is a real answer for Free lines)."""
    spec = rate("specific", "$2 per kg",
                [{"value": "2", "unit": "kg", "per": "kg"}])
    r = calculate(base_req(value="100", rate=spec))
    assert r["computable"] is False
    b = r["breakdown"]
    assert b["base_duty"] is None and b["mpf"] is None
    assert b["hmf"] is None and b["subtotal"] is None and b["total"] is None
    assert b["ch99_duties"] == []
    assert any("quantity" in w.lower() for w in r["warnings"])


def test_specific_cent_converted_to_dollars():
    """Regression (demo): 100 kg @ 9.35c/kg = $9.35, not $935."""
    spec = rate("specific", "9.35¢/kg",
                [{"value": "9.35", "unit": "cent", "per": "kg"}])
    r = calculate(base_req(value="1000", quantity="100",
                           quantity_unit="kg", rate=spec))
    assert r["computable"] is True
    assert r["breakdown"]["base_duty"] == Decimal("9.35")
    assert not any("differs" in w for w in r["warnings"])


def test_specific_dollar_per_kg():
    spec = rate("specific", "$2 per kg",
                [{"value": "2", "unit": "dollar", "per": "kg"}])
    r = calculate(base_req(value="100", quantity="10",
                           quantity_unit="kg", rate=spec))
    assert r["breakdown"]["base_duty"] == Decimal("20")
