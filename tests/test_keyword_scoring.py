"""
Worth It scoring tests. The weights are heuristic; what's pinned here is the
behavior users depend on: ordering (better keyword -> higher score), banding,
the SERP penalty, and honest factors when data is missing. No network.
"""
from app.keyword_scoring import score_keyword


def test_dream_keyword_is_easy():
    w = score_keyword(volume=90500, difficulty=10, intent="transactional")
    assert w.band == "easy"
    assert w.score >= 7.5


def test_brutal_keyword_is_avoid():
    w = score_keyword(volume=30, difficulty=92, intent="navigational")
    assert w.band == "avoid"


def test_higher_difficulty_lowers_score():
    easy = score_keyword(volume=5000, difficulty=20, intent="commercial")
    hard = score_keyword(volume=5000, difficulty=80, intent="commercial")
    assert easy.score > hard.score


def test_transactional_beats_navigational():
    txn = score_keyword(volume=5000, difficulty=40, intent="transactional")
    nav = score_keyword(volume=5000, difficulty=40, intent="navigational")
    assert txn.score > nav.score


def test_ai_overview_and_ads_penalize():
    clean = score_keyword(volume=5000, difficulty=40, intent="commercial", serp_features={"ads": 0})
    crowded = score_keyword(
        volume=5000, difficulty=40, intent="commercial",
        serp_features={"ai_overview": True, "ads": 4},
    )
    assert clean.score > crowded.score
    assert any("AI Overview" in f for f in crowded.factors)
    assert any("4 ads" in f for f in crowded.factors)


def test_unknown_serp_is_flagged_not_penalized():
    w = score_keyword(volume=5000, difficulty=40, intent="commercial", serp_features=None)
    assert any("not checked" in f for f in w.factors)


def test_missing_volume_scores_low_with_honest_factor():
    w = score_keyword(volume=None, difficulty=None, intent=None)
    assert w.score <= 4.0
    assert any("No search volume" in f for f in w.factors)


def test_score_bounds():
    w = score_keyword(volume=2_000_000, difficulty=0, intent="transactional")
    assert 0 <= w.score <= 10
