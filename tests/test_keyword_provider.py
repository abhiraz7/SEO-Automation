"""
Provider-router tests: Semrush-first routing, DataForSEO fallback, the
5-minute rate-limit cooldown, and the ok/no_data/error contract. Both
adapters are mocked -- no network.
"""
from unittest.mock import patch

import pytest

from app import keyword_provider

SEMRUSH_OK = {"Ph": "coffee", "Nq": "1000", "Cp": "1.2", "Co": "0.5", "Kd": "35", "error": None}
DFS_OK = {
    "keyword": "coffee",
    "keyword_info": {"search_volume": 900, "cpc": 1.1},
    "keyword_properties": {"keyword_difficulty": 30},
    "search_intent_info": {"main_intent": "informational"},
}


@pytest.fixture(autouse=True)
def reset_cooldown():
    """Cooldown is module-global state -- reset it so tests don't leak into
    each other (same reason you reset a shared fixture between test cases)."""
    keyword_provider._SEMRUSH_COOLDOWN_UNTIL = 0.0
    yield
    keyword_provider._SEMRUSH_COOLDOWN_UNTIL = 0.0


def test_semrush_success_short_circuits():
    with patch.object(keyword_provider.semrush, "fetch_keyword_overview", return_value=SEMRUSH_OK), \
         patch.object(keyword_provider.dataforseo, "fetch_keyword_overview") as dfs:
        result = keyword_provider.get_keyword_overview("coffee")
    assert result.status == "ok"
    assert result.source == "semrush"
    assert result.volume == 1000
    dfs.assert_not_called()


def test_semrush_error_falls_back_to_dataforseo():
    with patch.object(keyword_provider.semrush, "fetch_keyword_overview", return_value={"error": "boom"}), \
         patch.object(keyword_provider.dataforseo, "fetch_keyword_overview", return_value=DFS_OK):
        result = keyword_provider.get_keyword_overview("coffee")
    assert result.status == "ok"
    assert result.source == "dataforseo"
    assert result.volume == 900


def test_semrush_no_data_still_tries_dataforseo():
    with patch.object(keyword_provider.semrush, "fetch_keyword_overview", return_value={"no_data": True}), \
         patch.object(keyword_provider.dataforseo, "fetch_keyword_overview", return_value=DFS_OK):
        result = keyword_provider.get_keyword_overview("coffee")
    assert result.status == "ok"
    assert result.source == "dataforseo"


def test_both_providers_error():
    with patch.object(keyword_provider.semrush, "fetch_keyword_overview", return_value={"error": "no key"}), \
         patch.object(keyword_provider.dataforseo, "fetch_keyword_overview", return_value={"error": "no creds"}):
        result = keyword_provider.get_keyword_overview("coffee")
    assert result.status == "error"
    assert "semrush: no key" in result.error
    assert "dataforseo: no creds" in result.error


def test_both_providers_no_data():
    with patch.object(keyword_provider.semrush, "fetch_keyword_overview", return_value={"no_data": True}), \
         patch.object(keyword_provider.dataforseo, "fetch_keyword_overview", return_value={"no_data": True}):
        result = keyword_provider.get_keyword_overview("xyzzy")
    assert result.status == "no_data"
    assert result.error is None


def test_rate_limit_triggers_cooldown():
    """A 429 must put Semrush on cooldown so the NEXT lookup skips it entirely."""
    rate_limited = {"error": "phrase_all: HTTP 429", "rate_limited": True}
    with patch.object(keyword_provider.semrush, "fetch_keyword_overview", return_value=rate_limited) as sem, \
         patch.object(keyword_provider.dataforseo, "fetch_keyword_overview", return_value=DFS_OK):
        first = keyword_provider.get_keyword_overview("coffee")
        second = keyword_provider.get_keyword_overview("coffee")
    assert first.status == "ok" and first.source == "dataforseo"
    assert second.status == "ok" and second.source == "dataforseo"
    sem.assert_called_once()  # cooldown active -> Semrush not retried
    assert not keyword_provider._semrush_available()


def test_cooldown_expires():
    keyword_provider._mark_semrush_cooldown()
    assert not keyword_provider._semrush_available()
    # Simulate the 300s window passing rather than sleeping through it.
    keyword_provider._SEMRUSH_COOLDOWN_UNTIL = 0.0
    assert keyword_provider._semrush_available()


def test_bulk_returns_row_for_every_keyword():
    semrush_rows = {
        "a": {"error": "boom"},
        "b": {"Ph": "b", "Nq": "5", "Kd": "1", "error": None},
        "c": {"no_data": True},
    }
    dfs_rows = {"a": DFS_OK | {"keyword": "a"}, "c": {"no_data": True}}
    with patch.object(keyword_provider.semrush, "fetch_keywords_bulk", return_value=semrush_rows), \
         patch.object(keyword_provider.dataforseo, "fetch_keywords_bulk", return_value=dfs_rows) as dfs:
        results = keyword_provider.get_keywords_bulk(["a", "b", "c"])
    assert [r.keyword for r in results] == ["a", "b", "c"]
    assert [r.status for r in results] == ["ok", "ok", "no_data"]
    # Only the keywords Semrush couldn't answer go to DataForSEO.
    dfs.assert_called_once()
    assert dfs.call_args[0][0] == ["a", "c"]


def test_semrush_no_data_plus_dataforseo_error_is_no_data():
    """Semrush answered (empty index for this keyword+location); a dead
    fallback provider must not upgrade that to 'lookup failed'."""
    with patch.object(keyword_provider.semrush, "fetch_keyword_overview", return_value={"no_data": True}), \
         patch.object(keyword_provider.dataforseo, "fetch_keyword_overview", return_value={"error": "403 unverified"}):
        result = keyword_provider.get_keyword_overview("obscure phrase")
    assert result.status == "no_data"
    assert result.error is None


def test_bulk_semrush_no_data_plus_dataforseo_error_is_no_data():
    semrush_rows = {"a": {"no_data": True}, "b": {"error": "boom"}}
    dfs_rows = {"a": {"error": "403 unverified"}, "b": {"error": "403 unverified"}}
    with patch.object(keyword_provider.semrush, "fetch_keywords_bulk", return_value=semrush_rows), \
         patch.object(keyword_provider.dataforseo, "fetch_keywords_bulk", return_value=dfs_rows):
        results = keyword_provider.get_keywords_bulk(["a", "b"])
    assert [r.status for r in results] == ["no_data", "error"]


def test_serp_falls_back_to_semrush():
    sem_serp = {"keyword": "coffee", "items": [{"type": "organic", "rank_absolute": 1, "url": "https://x.com"}]}
    with patch.object(keyword_provider.dataforseo, "fetch_serp", return_value={"error": "403"}), \
         patch.object(keyword_provider.semrush, "fetch_serp", return_value=sem_serp):
        result = keyword_provider.get_serp("coffee")
    assert result == sem_serp


def test_serp_both_providers_down_reports_both_errors():
    with patch.object(keyword_provider.dataforseo, "fetch_serp", return_value={"error": "403"}), \
         patch.object(keyword_provider.semrush, "fetch_serp", return_value={"error": "no key"}):
        result = keyword_provider.get_serp("coffee")
    assert "dataforseo: 403" in result["error"]
    assert "semrush: no key" in result["error"]


def test_unsupported_location_is_an_error():
    result = keyword_provider.get_keyword_overview("dentist", "XX")
    assert result.status == "error"
    assert "Unsupported location" in result.error
