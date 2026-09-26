"""
SERP evidence: normalisation, classification and competitor selection.
No network: keyword_provider.get_serp is mocked.
"""
from unittest.mock import patch

import pytest

from app import keyword_provider
from app.services import serp_evidence as se


def _organic(pos, url, title="A title", desc="A description"):
    return {"type": "organic", "rank_group": pos, "rank_absolute": pos, "url": url, "title": title, "description": desc}


def _dfs_result(items, features=None):
    return {"items": items, "features": features or {}, "_source": "dataforseo"}


# ── classification ────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://example.com/guide/b-ed-eligibility", "direct_content"),
    ("https://www.reddit.com/r/india/comments/abc", "forum"),
    ("https://www.quora.com/What-is-B-Ed", "forum"),
    ("https://example.com/forum/thread/123", "forum"),
    ("https://example.com/community/topic-9", "forum"),
    ("https://www.amazon.in/dp/B00XYZ", "marketplace"),
    ("https://www.flipkart.com/some-book", "marketplace"),
    ("https://www.youtube.com/watch?v=abc", "social"),
    ("https://www.linkedin.com/pulse/x", "social"),
    ("https://example.com/", "homepage"),
    ("https://example.com", "homepage"),
    ("https://example.com/index.php", "homepage"),
    ("https://example.com/category/exams", "category"),
    ("https://example.com/tag/ctet", "category"),
    ("https://example.com/blog?s=ctet", "category"),
    ("https://example.com/products/notes-pdf", "category"),
    ("https://ncte.gov.in/website/rules.aspx", "government"),
    ("https://www.nic.in/notice/1", "government"),
    ("https://www.bbc.com/news/education-1", "publisher"),
    ("https://example.com/news/exam-dates", "publisher"),
    ("", "other"),
])
def test_classify_result(url, expected):
    assert se.classify_result(url) == expected


def test_a_big_brand_article_is_not_excluded_as_a_format():
    assert se.classify_result("https://www.forbes.com/advisor/education/guide") == "publisher"
    assert "publisher" not in se.EXCLUDED_CLASSES


def test_subdomain_of_a_social_domain_is_social():
    assert se.classify_result("https://m.facebook.com/page") == "social"


# ── normalisation ─────────────────────────────────────────────────────────

def test_normalize_full_dataforseo_result():
    raw = _dfs_result([
        _organic(2, "https://b.com/x", "B title"),
        _organic(1, "https://a.com/x", "A title"),
        {"type": "people_also_ask", "items": [{"type": "people_also_ask_element", "title": "What is B.Ed?"}, {"title": "Fees?"}]},
        {"type": "related_searches", "items": ["b ed syllabus", {"title": "b ed fees"}]},
        {"type": "featured_snippet", "title": "Snippet", "url": "https://a.com/x", "description": "d"},
        {"type": "paid", "url": "https://ad.com"},
    ], features={"people_also_ask": True, "ads": 1})
    out = se.normalize_serp(raw)
    assert out["status"] == "ok" and out["depth"] == "full" and out["source"] == "dataforseo"
    assert [r["url"] for r in out["organic"]] == ["https://a.com/x", "https://b.com/x"]  # sorted by position
    assert out["organic"][0]["title"] == "A title" and out["organic"][0]["domain"] == "a.com"
    assert out["paa"] == ["What is B.Ed?", "Fees?"]
    assert out["related"] == ["b ed syllabus", "b ed fees"]
    assert out["featured_snippet"]["title"] == "Snippet"
    assert out["features"] == {"people_also_ask": True, "ads": 1}


def test_paid_results_are_not_organic():
    out = se.normalize_serp(_dfs_result([{"type": "paid", "url": "https://ad.com"}, _organic(1, "https://a.com/x")]))
    assert [r["url"] for r in out["organic"]] == ["https://a.com/x"]


def test_duplicate_urls_and_missing_urls_are_dropped():
    out = se.normalize_serp(_dfs_result([_organic(1, "https://a.com/x"), _organic(2, "https://a.com/x"), {"type": "organic", "rank_group": 3}]))
    assert len(out["organic"]) == 1


def test_at_most_ten_organic_results_are_kept():
    out = se.normalize_serp(_dfs_result([_organic(i, f"https://s{i}.com/p") for i in range(1, 16)]))
    assert len(out["organic"]) == se.MAX_ORGANIC


def test_thin_semrush_result_is_flagged_and_has_no_fake_titles():
    raw = {"_source": "semrush", "items": [{"type": "organic", "rank_absolute": 1, "title": "a.com", "url": "https://a.com/guide", "description": None}]}
    out = se.normalize_serp(raw)
    assert out["status"] == "ok" and out["depth"] == "thin"
    assert out["organic"][0]["title"] is None  # the domain is not a title
    assert out["paa"] == [] and out["related"] == []


def test_error_is_an_error_not_an_empty_serp():
    out = se.normalize_serp({"error": "dataforseo: 402; semrush: no key"})
    assert out["status"] == "error" and "402" in out["error"] and out["organic"] == []


def test_no_organic_results_is_no_data_not_error_and_not_ok():
    out = se.normalize_serp(_dfs_result([{"type": "people_also_ask", "items": [{"title": "Q?"}]}]))
    assert out["status"] == "no_data" and out["error"]


@pytest.mark.parametrize("bad", [None, [], "x", 5])
def test_unexpected_provider_response_never_raises(bad):
    assert se.normalize_serp(bad)["status"] == "error"


# ── fetch_serp_evidence ───────────────────────────────────────────────────

def test_fetch_uses_the_existing_provider_adapter():
    with patch.object(keyword_provider, "get_serp", return_value=_dfs_result([_organic(1, "https://a.com/x")])) as m:
        out = se.fetch_serp_evidence("b ed eligibility", "IN", "mobile")
    m.assert_called_once_with("b ed eligibility", "IN", device="mobile")
    assert out["status"] == "ok"


def test_provider_exception_becomes_error_status():
    with patch.object(keyword_provider, "get_serp", side_effect=RuntimeError("boom")):
        out = se.fetch_serp_evidence("kw")
    assert out["status"] == "error" and "boom" in out["error"]


def test_empty_keyword_is_an_error_without_calling_the_provider():
    with patch.object(keyword_provider, "get_serp") as m:
        out = se.fetch_serp_evidence("   ")
    m.assert_not_called()
    assert out["status"] == "error"


def test_provider_error_dict_passes_through():
    with patch.object(keyword_provider, "get_serp", return_value={"error": "dataforseo: down"}):
        out = se.fetch_serp_evidence("kw")
    assert out["status"] == "error" and "down" in out["error"]


# ── competitor selection ──────────────────────────────────────────────────

def _rows(*urls):
    return se.normalize_serp(_dfs_result([_organic(i, u) for i, u in enumerate(urls, start=1)]))["organic"]


def test_forums_marketplaces_social_homepages_and_categories_are_excluded_with_reasons():
    rows = _rows(
        "https://www.reddit.com/r/x/comments/1", "https://www.amazon.in/dp/1", "https://www.youtube.com/watch?v=1",
        "https://home.com/", "https://cat.com/category/x", "https://good.com/guide",
    )
    selected, excluded = se.select_competitors(rows, "https://mine.com/page")
    assert [r["domain"] for r in selected] == ["good.com"]
    assert len(excluded) == 5 and all(e["excluded_reason"] for e in excluded)
    assert any("forum" in e["excluded_reason"] for e in excluded)


def test_the_targets_own_domain_is_never_a_competitor():
    rows = _rows("https://mine.com/other-page", "https://www.mine.com/x", "https://rival.com/guide")
    selected, excluded = se.select_competitors(rows, "https://www.mine.com/page")
    assert [r["domain"] for r in selected] == ["rival.com"]
    assert all(e["excluded_reason"] == "the target's own domain" for e in excluded)


def test_one_page_per_domain_the_best_ranked():
    rows = _rows("https://a.com/one", "https://a.com/two", "https://b.com/one")
    selected, excluded = se.select_competitors(rows, "https://mine.com/p")
    assert [r["url"] for r in selected] == ["https://a.com/one", "https://b.com/one"]
    assert "same domain" in excluded[0]["excluded_reason"]


def test_at_most_seven_and_the_extras_are_explained():
    rows = _rows(*[f"https://s{i}.com/guide" for i in range(1, 11)])
    selected, excluded = se.select_competitors(rows, "https://mine.com/p")
    assert len(selected) == 7 and [r["position"] for r in selected] == list(range(1, 8))
    assert len(excluded) == 3 and all("beyond" in e["excluded_reason"] for e in excluded)


def test_ordinary_content_pages_are_preferred_over_publishers_but_publishers_are_kept():
    rows = _rows("https://www.bbc.com/news/a", "https://a.com/guide", "https://b.com/guide")
    selected, _ = se.select_competitors(rows, "https://mine.com/p", max_n=2)
    assert [r["domain"] for r in selected] == ["a.com", "b.com"]  # publisher ranks first but is filled last
    selected, _ = se.select_competitors(rows, "https://mine.com/p", max_n=3)
    assert "bbc.com" in [r["domain"] for r in selected]


def test_fewer_than_the_maximum_is_fine_and_reports_the_true_count():
    rows = _rows("https://a.com/guide", "https://b.com/guide")
    selected, _ = se.select_competitors(rows, "https://mine.com/p")
    assert len(selected) == 2


def test_nothing_usable_returns_empty_selection_not_an_error():
    rows = _rows("https://www.reddit.com/r/x/comments/1", "https://www.amazon.in/dp/1")
    selected, excluded = se.select_competitors(rows, "https://mine.com/p")
    assert selected == [] and len(excluded) == 2
