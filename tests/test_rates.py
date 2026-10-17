"""Tests for rag/rates.py -- every example from the Module A spec."""

import pytest

from rag.rates import normalize_unit, parse_rate, parse_special, split_compound


def _no_floats(obj):
    """Recursively assert no float appears anywhere in ``obj``."""
    if isinstance(obj, float):
        raise AssertionError(f"float found: {obj!r}")
    if isinstance(obj, dict):
        for v in obj.values():
            _no_floats(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _no_floats(v)


def test_parse_rate_never_returns_float():
    raws = [
        "Free", "2.5%", "6.8%", "1¢/kg", "0.34¢/liter", "2.8¢/doz.",
        "17¢ each + 2.5% + 1¢/jewel", "$1.104/kg + 14.9%",
        "46.3¢/kg + 14.9%", "13.5¢/kg + 6.3% + 1.9¢/article",
        "$4.50 each + 65% + 25¢/jewel", "90%", "35%",
        "See 9902...", "See 9903.90.08", "The rate applicable to...",
        "A duty upon the value of repairs...", "No change", "",
        "See 9822.04.25 (AU)",
        "The duty provided in the applicable subheading plus 25%",
    ]
    for raw in raws:
        result = parse_rate(raw)
        _no_floats(result)
        for comp in result["components"]:
            assert isinstance(comp["value"], str)
        # Values must be Decimal-parseable strings.
        from decimal import Decimal

        for comp in result["components"]:
            Decimal(comp["value"])


def test_free():
    r = parse_rate("Free")
    assert r["kind"] == "free"
    assert r["components"] == []
    assert r["requires_quantity"] is False
    assert r["cross_ref_target"] is None


def test_ad_valorem():
    r = parse_rate("2.5%")
    assert r["kind"] == "ad_valorem"
    assert r["components"] == [{"value": "2.5", "unit": "percent", "per": "value"}]
    assert r["requires_quantity"] is False
    assert parse_rate("6.8%")["components"][0]["value"] == "6.8"
    assert parse_rate("90%")["kind"] == "ad_valorem"
    assert parse_rate("35%")["components"][0]["value"] == "35"


def test_specific_cent_per_kg():
    r = parse_rate("1¢/kg")
    assert r["kind"] == "specific"
    assert r["components"] == [{"value": "1", "unit": "cent", "per": "kg"}]
    assert r["requires_quantity"] is True


def test_specific_cent_per_liter():
    r = parse_rate("0.34¢/liter")
    assert r["kind"] == "specific"
    assert r["components"] == [{"value": "0.34", "unit": "cent", "per": "liter"}]


def test_specific_cent_per_doz():
    r = parse_rate("2.8¢/doz.")
    assert r["kind"] == "specific"
    assert r["components"] == [{"value": "2.8", "unit": "cent", "per": "doz"}]


def test_compound_three_parts():
    r = parse_rate("17¢ each + 2.5% + 1¢/jewel")
    assert r["kind"] == "compound"
    assert r["components"] == [
        {"value": "17", "unit": "cent", "per": "each"},
        {"value": "2.5", "unit": "percent", "per": "value"},
        {"value": "1", "unit": "cent", "per": "jewel"},
    ]
    assert r["requires_quantity"] is True


def test_compound_dollar_plus_percent():
    r = parse_rate("$1.104/kg + 14.9%")
    assert r["kind"] == "compound"
    assert r["components"] == [
        {"value": "1.104", "unit": "dollar", "per": "kg"},
        {"value": "14.9", "unit": "percent", "per": "value"},
    ]


def test_compound_cent_plus_percent():
    r = parse_rate("46.3¢/kg + 14.9%")
    assert r["kind"] == "compound"
    assert r["components"][0] == {"value": "46.3", "unit": "cent", "per": "kg"}
    assert r["components"][1] == {
        "value": "14.9",
        "unit": "percent",
        "per": "value",
    }


def test_compound_three_mixed():
    r = parse_rate("13.5¢/kg + 6.3% + 1.9¢/article")
    assert r["kind"] == "compound"
    assert len(r["components"]) == 3
    assert r["components"][2] == {"value": "1.9", "unit": "cent", "per": "article"}


def test_compound_col2_watches():
    r = parse_rate("$4.50 each + 65% + 25¢/jewel")
    assert r["kind"] == "compound"
    assert r["components"] == [
        {"value": "4.50", "unit": "dollar", "per": "each"},
        {"value": "65", "unit": "percent", "per": "value"},
        {"value": "25", "unit": "cent", "per": "jewel"},
    ]


def test_cross_ref_see():
    r = parse_rate("See 9902...")
    assert r["kind"] == "cross_ref"
    assert r["cross_ref_target"] == "9902"
    r = parse_rate("See 9903.90.08")
    assert r["kind"] == "cross_ref"
    assert r["cross_ref_target"] == "9903.90.08"


def test_cross_ref_rate_applicable():
    r = parse_rate("The rate applicable to the article in subheading 8708.99.68")
    assert r["kind"] == "cross_ref"
    assert r["cross_ref_target"] == "8708.99.68"


def test_cross_ref_applicable_subheading_plus():
    r = parse_rate("The duty provided in the applicable subheading plus 25%")
    assert r["kind"] == "compound"
    assert r["components"] == [
        {"value": "25", "unit": "percent", "per": "value"}
    ]
    assert "applicable subheading" in r["note"]


def test_uncomputable_repairs():
    r = parse_rate("A duty upon the value of repairs made abroad")
    assert r["kind"] == "uncomputable"
    assert "repairs" in r["note"].lower()


def test_no_change():
    r = parse_rate("No change")
    assert r["kind"] == "uncomputable"
    assert "no change" in r["note"].lower()


def test_empty():
    assert parse_rate("")["kind"] == "empty"
    assert parse_rate("   ")["kind"] == "empty"
    assert parse_rate(None)["kind"] == "empty"


def test_split_compound_respects_parens():
    assert split_compound("2.5% + Free (A+, B)") == ["2.5%", "Free (A+, B)"]
    assert split_compound("a (x + y) + b") == ["a (x + y)", "b"]
    assert split_compound("17¢ each + 2.5% + 1¢/jewel") == [
        "17¢ each",
        "2.5%",
        "1¢/jewel",
    ]


def test_normalize_unit():
    assert normalize_unit("doz.") == "doz"
    assert normalize_unit("No.") == "number"
    assert normalize_unit("Jwls.") == "jewel"
    assert normalize_unit("KG") == "kg"
    assert normalize_unit("each") == "each"


def test_parse_special_free_programs():
    out = parse_special("Free (A, AU, BH, CL)")
    assert [p["symbol"] for p in out["programs"]] == ["A", "AU", "BH", "CL"]
    for p in out["programs"]:
        assert p["rate"]["kind"] == "free"
    assert out["raw"] == "Free (A, AU, BH, CL)"


def test_parse_special_a_plus_symbol():
    out = parse_special("Free (A+, AU, BH)")
    assert out["programs"][0]["symbol"] == "A+"


def test_parse_special_cross_ref_program():
    out = parse_special("See 9822.04.25 (AU)")
    assert len(out["programs"]) == 1
    assert out["programs"][0]["symbol"] == "AU"
    assert out["programs"][0]["rate"]["kind"] == "cross_ref"
    assert out["programs"][0]["rate"]["cross_ref_target"] == "9822.04.25"


def test_parse_special_empty_and_no_change():
    assert parse_special("") == {"programs": [], "raw": ""}
    assert parse_special("No change")["programs"] == []


def test_parse_special_bare_free():
    out = parse_special("Free")
    assert len(out["programs"]) == 1
    assert out["programs"][0]["rate"]["kind"] == "free"
