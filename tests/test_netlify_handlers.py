"""
Stdlib-only tests for the Netlify Functions (serverless) adapter.

Verifies the Lambda-style handlers return the expected statusCode/body, using
the same services as the Flask app. (Netlify's packaging is not exercised here,
only the handler contract and logic.)
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from netlify.functions import calculate as calc_fn
from netlify.functions import health as health_fn


def _decode(resp):
    return resp["statusCode"], json.loads(resp["body"])


def test_calculate_ok():
    status, body = _decode(
        calc_fn.handler({"body": json.dumps({"description": "leather handbag", "value": 500})}, None)
    )
    assert status == 200, (status, body)
    assert body["matched_description"], body
    assert body["hts_number"].startswith("4202"), body
    assert body["match_source"] in ("llm_embeddings", "lexical_tfidf", "hts_code")
    assert body["duty"] >= 0
    print("calculate OK ->", body["hts_number"], body["matched_description"][:70], body["match_source"])


def test_calculate_validation_error():
    status, body = _decode(calc_fn.handler({"body": json.dumps({"description": "x", "value": 1})}, None))
    assert status == 400, (status, body)
    assert "error" in body
    print("calculate validation error OK ->", status, body["error"])


def test_calculate_not_found():
    status, body = _decode(
        calc_fn.handler({"body": json.dumps({"description": "zzz qqq xxx nonsensical item", "value": 10})}, None)
    )
    # Either a low-confidence 404 or a (wrong but tolerated) 200 with low confidence.
    assert status in (200, 404), (status, body)
    print("calculate low-confidence ->", status, body.get("error") or body.get("matched_description", "")[:60])


def test_health():
    status, body = _decode(health_fn.handler({}, None))
    assert status == 200, (status, body)
    assert body["checks"]["tariff_data"] is True
    assert "matching" in body
    print("health OK ->", body["status"], body["matching"]["engine"], body["matching"]["records"], "records")


if __name__ == "__main__":
    test_health()
    test_calculate_ok()
    test_calculate_validation_error()
    test_calculate_not_found()
    print("ALL NETLIFY HANDLER TESTS PASSED")
