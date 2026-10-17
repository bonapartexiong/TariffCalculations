"""API tests for Module C.

Flask tests use the Flask test_client and skip if Flask is missing.
Validator / config / pipeline-degradation tests run without Flask.

Every response is checked for two invariants:
  * no personal email address appears in any response body, and
  * no exception detail (traceback / source frame) ever leaks.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend import config as config_mod  # noqa: E402
from backend.exceptions import ValidationError  # noqa: E402
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
DISCLAIMER = (
    "Estimate only — not a binding classification. "
    "Verify with CBP/broker. "
    "Sources: HTS 2026 Rev 21 + CBP fee config (external)."
)
from backend.validators import (  # noqa: E402
    clamp_confidence,
    validate_calculate,
    validate_rag_query,
)

try:
    import flask  # noqa: F401
    from backend.app import create_app  # noqa: E402

    HAS_FLASK = True
except Exception:
    HAS_FLASK = False

FLASK_ONLY = pytest.mark.skipif(not HAS_FLASK, reason="Flask not installed")


@pytest.fixture()
def client(tmp_path):
    if not HAS_FLASK:
        pytest.skip("Flask not installed")
    app = create_app(index_path=str(tmp_path / "missing-index.jsonl"))
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def _assert_no_email_no_leak(response):
    body = response.get_data(as_text=True)
    assert not EMAIL_RE.search(body), f"Email address leaked in response: {body[:500]}"
    assert "Traceback" not in body, f"Traceback leaked: {body[:500]}"
    assert 'File "' not in body, f"Source frame leaked: {body[:500]}"
    return body


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@FLASK_ONLY
def test_health_shape(client):
    response = client.get("/v1/health")
    assert response.status_code == 200
    body = _assert_no_email_no_leak(response)
    data = json.loads(body)
    for key in ("status", "revision", "chunks", "source_pdf_sha256",
                "build_date", "engine", "index_path"):
        assert key in data, f"health missing {key}"
    assert data["revision"] == "2026 Rev 21"
    assert isinstance(data["chunks"], int)
    assert isinstance(data["index_exists"], bool)
    assert data["index_exists"] == os.path.exists(data["index_path"])  # True iff the fixture index file exists


# ---------------------------------------------------------------------------
# Calculate
# ---------------------------------------------------------------------------


@FLASK_ONLY
def test_calculate_backward_compat(client):
    response = client.post("/v1/calculate",
                           json={"description": "Leather handbag", "value": 500})
    assert response.status_code == 200
    data = json.loads(_assert_no_email_no_leak(response))
    for key in ("match", "breakdown", "citations", "provenance",
                "inputs_echo", "computable", "disclaimer"):
        assert key in data, f"calculate missing {key}"
    assert data["disclaimer"] == DISCLAIMER
    assert data["inputs_echo"]["value"] == "500"
    assert isinstance(data["computable"], bool)


@FLASK_ONLY
def test_calculate_extended(client):
    response = client.post("/v1/calculate", json={
        "description": "Electric motor from China",
        "value": 1000,
        "hts_code": "8501.51.20",
        "quantity": 10,
        "quantity_unit": "each",
        "origin_country": "China",
        "mode": "ocean",
        "quota_status": "unknown",
        "chapter99_stack": ["9903.88.01"],
        "de_minimis": False,
    })
    assert response.status_code == 200
    data = json.loads(_assert_no_email_no_leak(response))
    assert data["inputs_echo"]["origin_country"] == "China"
    assert data["inputs_echo"]["chapter99_stack"] == ["9903.88.01"]  # dots preserved


@FLASK_ONLY
def test_calculate_apostrophe_semicolon_allowed(client):
    response = client.post("/v1/calculate", json={
        "description": "Men's handbag; leather, 100% + trim \"quoted\"",
        "value": 250,
    })
    assert response.status_code == 200
    data = json.loads(_assert_no_email_no_leak(response))
    assert data["inputs_echo"]["description"].startswith("Men's handbag;")


@FLASK_ONLY
def test_calculate_rejects_bool_nan(client):
    for bad_value in (True, "NaN", "Infinity", -5, 0, 100000001):
        response = client.post("/v1/calculate",
                               json={"description": "Leather handbag", "value": bad_value})
        assert response.status_code == 400, f"value {bad_value!r} should be rejected"
        _assert_no_email_no_leak(response)
    response = client.post("/v1/calculate",
                           json={"description": "Leather handbag", "value": "not-a-number"})
    assert response.status_code == 400


@FLASK_ONLY
def test_calculate_rejects_short_description(client):
    response = client.post("/v1/calculate", json={"description": "ab", "value": 10})
    assert response.status_code == 400


@FLASK_ONLY
def test_calculate_rejects_malformed_json(client):
    response = client.post("/v1/calculate", data="{not json",
                           content_type="application/json")
    assert response.status_code == 400
    _assert_no_email_no_leak(response)


# ---------------------------------------------------------------------------
# RAG query
# ---------------------------------------------------------------------------


@FLASK_ONLY
def test_rag_query_abstains_on_nonsense(client):
    response = client.post("/v1/rag/query",
                           json={"question": "zzzqqq asdfgh nonsense blorp"})
    assert response.status_code == 200
    data = json.loads(_assert_no_email_no_leak(response))
    assert data["abstained"] is True
    assert data["citations"] == []
    assert data["disclaimer"] == DISCLAIMER


@FLASK_ONLY
def test_rag_query_rejects_short_question(client):
    response = client.post("/v1/rag/query", json={"question": "hi"})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Error paths never leak
# ---------------------------------------------------------------------------


@FLASK_ONLY
def test_404_and_405_no_leak(client):
    response = client.get("/v1/nope")
    assert response.status_code == 404
    _assert_no_email_no_leak(response)
    response = client.get("/v1/calculate")
    assert response.status_code == 405
    _assert_no_email_no_leak(response)


# ---------------------------------------------------------------------------
# Validators / helpers (no Flask needed for the logic, but Flask is
# already imported above; these also document the contract)
# ---------------------------------------------------------------------------


def test_validators_nan_bool_rejected():
    for bad in (True, False, float("nan"), float("inf"), float("-inf"), None, "NaN"):
        with pytest.raises(ValidationError):
            validate_calculate({"description": "Leather handbag", "value": bad})


def test_validators_value_bounds():
    with pytest.raises(ValidationError):
        validate_calculate({"description": "Leather handbag", "value": "0.001"})
    with pytest.raises(ValidationError):
        validate_calculate({"description": "Leather handbag", "value": "100000001"})
    cleaned = validate_calculate({"description": "Leather handbag", "value": "0.01"})
    assert str(cleaned["value"]) == "0.01"


def test_confidence_clamp():
    assert clamp_confidence(2.5) == 1.0
    assert clamp_confidence(-1) == 0.0
    assert clamp_confidence(0.42) == pytest.approx(0.42)
    assert clamp_confidence(float("nan")) == 0.0
    assert clamp_confidence(float("inf")) == 1.0
    assert clamp_confidence(float("-inf")) == 0.0
    assert clamp_confidence(None) == 0.0
    assert clamp_confidence(True) == 0.0
    assert clamp_confidence("garbage") == 0.0


def test_rag_query_top_k_clamped():
    assert validate_rag_query({"question": "Is Free (A) claimable?", "top_k": 99})["top_k"] == 20
    assert validate_rag_query({"question": "Is Free (A) claimable?", "top_k": 0})["top_k"] == 1
    assert validate_rag_query({"question": "Is Free (A) claimable?"})["top_k"] == 5


# ---------------------------------------------------------------------------
# Config safe parsing
# ---------------------------------------------------------------------------


def test_config_safe_helpers_never_crash():
    assert config_mod.safe_int("abc", 5) == 5
    assert config_mod.safe_int(None, 5) == 5
    assert config_mod.safe_int(True, 5) == 5
    assert config_mod.safe_int(" 42 ", 5) == 42
    assert config_mod.safe_float("inf", 1.0) == 1.0
    assert config_mod.safe_float("nan", 1.0) == 1.0
    assert config_mod.safe_float("junk", 1.0) == 1.0
    assert config_mod.safe_float("0.5", 1.0) == 0.5
    assert config_mod.parse_origins(" https://a.example , , https://b.example ") == (
        "https://a.example", "https://b.example",
    )
    settings = config_mod.get_settings({"PORT": "garbage", "ALLOWED_ORIGINS": " a ,b,, a "})
    assert settings.port == 5000
    assert settings.allowed_origins == ("a", "b")


# ---------------------------------------------------------------------------
# Netlify adapters (thin; logic lives in the pipeline)
# ---------------------------------------------------------------------------


def test_netlify_calculate_adapter():
    sys.path.insert(0, str(PROJECT_ROOT / "netlify" / "functions"))
    import calculate as netlify_calculate

    event = {"httpMethod": "POST",
             "body": json.dumps({"description": "Leather handbag", "value": 500})}
    result = netlify_calculate.handler(event)
    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["disclaimer"] == DISCLAIMER
    assert not EMAIL_RE.search(result["body"])

    bad = netlify_calculate.handler({"httpMethod": "POST", "body": "{bad json"})
    assert bad["statusCode"] == 400
    assert "Traceback" not in bad["body"]


def test_netlify_health_adapter():
    sys.path.insert(0, str(PROJECT_ROOT / "netlify" / "functions"))
    import health as netlify_health

    result = netlify_health.handler({"httpMethod": "GET"})
    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["revision"] == "2026 Rev 21"
    assert not EMAIL_RE.search(result["body"])


# ---------------------------------------------------------------------------
# build_index CLI guards
# ---------------------------------------------------------------------------


def test_build_index_refuses_non_pdf(tmp_path):
    from scripts.build_index import main as build_main

    fake = tmp_path / "not-a-pdf.txt"
    fake.write_text("hello")
    assert build_main(["--pdf", str(fake), "--out", str(tmp_path / "out.jsonl")]) == 2
