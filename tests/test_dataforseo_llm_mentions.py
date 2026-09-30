"""
fetch_llm_mentions_target_metrics unit tests. _post is mocked -- no real
HTTP, no real DataForSEO account, no real spend. Response shapes here are
built from DataForSEO's documented target_metrics response structure
(docs.dataforseo.com/v3/ai_optimization/llm_mentions/target_metrics/live,
checked 2026-09-29) -- NOT yet verified against a real live call, since
this account has no AI Optimization API units purchased. See the
function's own docstring for that caveat.
"""
from unittest.mock import patch

import pytest

from app import dataforseo


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("DATAFORSEO_LOGIN", "user")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "pass")


def test_not_configured_is_error(monkeypatch):
    monkeypatch.delenv("DATAFORSEO_LOGIN", raising=False)
    monkeypatch.delenv("DATAFORSEO_PASSWORD", raising=False)
    result = dataforseo.fetch_llm_mentions_target_metrics("https://example.com")
    assert result["error"] == "DataForSEO not configured"


def test_unsupported_platform_is_error():
    result = dataforseo.fetch_llm_mentions_target_metrics("https://example.com", platform="claude")
    assert "Unsupported platform" in result["error"]


def test_unsupported_location_is_error():
    result = dataforseo.fetch_llm_mentions_target_metrics("https://example.com", location="Nowhereland")
    assert "Unsupported location" in result["error"]


def _ok_response(mentions=26465, ai_search_volume=1203984, sources=None):
    return {
        "status_code": 20000,
        "tasks": [{
            "status_code": 20000,
            "cost": 0.101,
            "result": [{
                "aggregated_metrics": {
                    "total": {"mentions": mentions, "ai_search_volume": ai_search_volume},
                    "sources_domain": sources or [{"key": "www.reddit.com", "mentions": 3978, "ai_search_volume": 233061}],
                }
            }],
        }],
    }


def test_ok_maps_total_and_sources():
    with patch.object(dataforseo, "_post", return_value=_ok_response()) as mock_post:
        result = dataforseo.fetch_llm_mentions_target_metrics("https://example.com")
    assert result["error"] is None
    assert result["total_mentions"] == 26465
    assert result["ai_search_volume"] == 1203984
    assert result["sources_domain"][0]["key"] == "www.reddit.com"
    assert result["cost"] == 0.101

    call_path, call_payload = mock_post.call_args[0]
    assert call_path == "/ai_optimization/llm_mentions/target_metrics/live"
    assert call_payload[0]["target"] == [{"domain": "example.com", "search_filter": "include"}]


def test_platform_filter_passed_through_when_not_both():
    with patch.object(dataforseo, "_post", return_value=_ok_response()) as mock_post:
        dataforseo.fetch_llm_mentions_target_metrics("https://example.com", platform="chat_gpt")
    _, call_payload = mock_post.call_args[0]
    assert call_payload[0]["platform"] == "chat_gpt"


def test_platform_both_omits_the_param():
    with patch.object(dataforseo, "_post", return_value=_ok_response()) as mock_post:
        dataforseo.fetch_llm_mentions_target_metrics("https://example.com", platform="both")
    _, call_payload = mock_post.call_args[0]
    assert "platform" not in call_payload[0]


def test_no_data_when_result_empty():
    response = _ok_response()
    response["tasks"][0]["result"] = []
    with patch.object(dataforseo, "_post", return_value=response):
        result = dataforseo.fetch_llm_mentions_target_metrics("https://example.com")
    assert result.get("no_data") is True
    assert result["error"] is None


def test_top_level_error_status():
    with patch.object(dataforseo, "_post", return_value={"status_code": 40100, "status_message": "Auth failed."}):
        result = dataforseo.fetch_llm_mentions_target_metrics("https://example.com")
    assert result["error"] == "Auth failed."


def test_task_level_error_status():
    with patch.object(dataforseo, "_post", return_value={
        "status_code": 20000,
        "tasks": [{"status_code": 40501, "status_message": "Invalid Field."}],
    }):
        result = dataforseo.fetch_llm_mentions_target_metrics("https://example.com")
    assert result["error"] == "Invalid Field."


def test_exception_from_post_is_error_not_crash():
    with patch.object(dataforseo, "_post", side_effect=Exception("network blip")):
        result = dataforseo.fetch_llm_mentions_target_metrics("https://example.com")
    assert "network blip" in result["error"]
