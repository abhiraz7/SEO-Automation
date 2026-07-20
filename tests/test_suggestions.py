"""
Suggestion de-duplication unit coverage (Task 3.5d). Only the pure
normalization/hash logic is unit-tested here -- _generate_and_store itself
needs a live DB session + Claude call and was instead verified live
against project 1's real data (see AgentLog): a real regeneration on an
issue with an already-deployed suggestion produced 2 new pending rows
instead of 3, confirming the duplicate candidate was dropped.
"""
from app.routes.suggestions import _normalize_content, content_hash


def test_normalize_collapses_whitespace_and_case():
    assert _normalize_content("  Hello   World  ") == "hello world"
    assert _normalize_content("Hello\nWorld") == "hello world"


def test_content_hash_matches_across_whitespace_and_case_variants():
    a = content_hash("VTraffic: Digital Marketing Agency for Small Business Growth")
    b = content_hash("  vtraffic:  digital marketing agency for small business growth ")
    assert a == b


def test_content_hash_differs_for_different_text():
    a = content_hash("VTraffic: Digital Marketing Agency for Small Business Growth")
    b = content_hash("SEO Services & Digital Marketing Solutions | VTraffic")
    assert a != b


def test_content_hash_handles_empty_string():
    assert content_hash("") == content_hash("   ")
