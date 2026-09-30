"""
Regression: in DataForSEO's real instant_pages response, `content` and
`social_media_tags` are nested INSIDE `meta` (verified live, 2026-09-30). The
code used to read them from the top level, so every page looked like it had no
Open Graph / Twitter tags, and word_count / og_title / twitter_card were never
stored. The fixture below is trimmed from a real response.
"""
from app import dataforseo_onpage as d
from app.jobs.handlers import verify_deploy


def real_shape_item(with_social=True):
    social = {
        "og:title": "Hindi Archives - ExamNotesPDF",
        "og:url": "https://example.com/subject/hindi/",
        "twitter:card": "summary_large_image",
        "twitter:title": "Hindi Archives - ExamNotesPDF",
    } if with_social else {}
    return {
        "url": "https://example.com/subject/hindi/",
        "onpage_score": 94.51,
        "size": 66836,
        "meta": {
            "title": "Hindi Archives - ExamNotesPDF",
            "description": None,
            "canonical": "https://example.com/subject/hindi/",
            "htags": {"h1": ["Hindi"], "h2": ["a", "b"], "h3": ["c"], "h4": ["d"]},
            "internal_links_count": 49,
            "content": {
                "plain_text_size": 1508,
                "plain_text_rate": 0.023160804791890647,
                "plain_text_word_count": 343,
                "flesch_kincaid_readability_index": 64.57,
            },
            "social_media_tags": social,
        },
        "checks": {"no_description": True, "low_content_rate": True, "canonical": True, "is_https": True},
    }


def _categories(issues):
    return {(i["category"], i["rule"]) for i in issues}


def test_pages_with_og_and_twitter_tags_are_not_flagged_missing():
    found = _categories(d.issues_from_item(real_shape_item()))
    assert ("opengraph", "missing") not in found
    assert ("twitter", "missing") not in found


def test_pages_genuinely_without_social_tags_are_still_flagged():
    found = _categories(d.issues_from_item(real_shape_item(with_social=False)))
    assert ("opengraph", "missing") in found and ("twitter", "missing") in found


def test_normalize_page_stores_word_count_and_social_fields():
    page = d.normalize_page(real_shape_item())
    assert page["word_count"] == 343
    assert page["og_title"] == "Hindi Archives - ExamNotesPDF"
    assert page["twitter_card"] == "summary_large_image"


def test_older_flat_shape_still_works():
    item = real_shape_item()
    flat = {**item, "content": item["meta"].pop("content"), "social_media_tags": item["meta"].pop("social_media_tags")}
    assert d.normalize_page(flat)["word_count"] == 343
    assert ("opengraph", "missing") not in _categories(d.issues_from_item(flat))


def test_deploy_verification_reads_live_social_values():
    item = real_shape_item()
    assert verify_deploy._extract_live_value("opengraph", item) == "Hindi Archives - ExamNotesPDF"
    assert verify_deploy._extract_live_value("twitter", item) == "Hindi Archives - ExamNotesPDF"
