from app import models
from app.services.ai_visibility_score import compute_ai_visibility_score


def _check(ai_overview_present=False, brand_in_ai_overview=False, organic_rank=None, raw_error=None):
    return models.VisibilityCheck(
        project_id=1, query="q",
        ai_overview_present=ai_overview_present,
        brand_in_ai_overview=brand_in_ai_overview,
        organic_rank=organic_rank,
        raw_error=raw_error,
    )


def test_no_checks_is_no_data():
    result = compute_ai_visibility_score([])
    assert result.score is None
    assert result.has_data is False
    assert result.total_checks == 0


def test_checks_but_no_ai_overview_ever_is_no_data():
    checks = [_check(ai_overview_present=False), _check(ai_overview_present=False)]
    result = compute_ai_visibility_score(checks)
    assert result.score is None
    assert result.has_data is False
    assert result.total_checks == 2


def test_cited_in_every_ai_overview_is_100():
    checks = [
        _check(ai_overview_present=True, brand_in_ai_overview=True),
        _check(ai_overview_present=True, brand_in_ai_overview=True),
    ]
    result = compute_ai_visibility_score(checks)
    assert result.score == 100.0
    assert result.checks_with_ai_overview == 2
    assert result.checks_cited == 2


def test_never_cited_is_0():
    checks = [
        _check(ai_overview_present=True, brand_in_ai_overview=False),
        _check(ai_overview_present=True, brand_in_ai_overview=False),
    ]
    result = compute_ai_visibility_score(checks)
    assert result.score == 0.0


def test_partial_citation_percentage():
    checks = [
        _check(ai_overview_present=True, brand_in_ai_overview=True),
        _check(ai_overview_present=True, brand_in_ai_overview=False),
        _check(ai_overview_present=True, brand_in_ai_overview=False),
        _check(ai_overview_present=True, brand_in_ai_overview=False),
    ]
    result = compute_ai_visibility_score(checks)
    assert result.score == 25.0


def test_queries_without_ai_overview_excluded_from_denominator():
    """The core honesty rule: a query where Google never showed an AI
    Overview must not drag the score down -- it's Google's behavior, not
    the project's visibility."""
    checks = [
        _check(ai_overview_present=True, brand_in_ai_overview=True),
        _check(ai_overview_present=False),  # no AI Overview at all -- excluded
        _check(ai_overview_present=False),
        _check(ai_overview_present=False),
    ]
    result = compute_ai_visibility_score(checks)
    assert result.score == 100.0  # not 25.0
    assert result.total_checks == 4
    assert result.checks_with_ai_overview == 1


def test_failed_checks_excluded_entirely():
    checks = [
        _check(ai_overview_present=True, brand_in_ai_overview=True),
        _check(raw_error="timeout"),
    ]
    result = compute_ai_visibility_score(checks)
    assert result.total_checks == 1
    assert result.score == 100.0


def test_avg_organic_rank_ignores_missing_values():
    checks = [_check(organic_rank=4), _check(organic_rank=None), _check(organic_rank=8)]
    result = compute_ai_visibility_score(checks)
    assert result.avg_organic_rank == 6.0


def test_avg_organic_rank_none_when_never_ranked():
    checks = [_check(organic_rank=None), _check(organic_rank=None)]
    result = compute_ai_visibility_score(checks)
    assert result.avg_organic_rank is None
