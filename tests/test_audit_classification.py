"""
Audit Semantics v2/v3: the thing this whole refactor exists to prove is that
a page with no canonical/OpenGraph/Twitter-card/meta-description -- all
normal, valid states per Google's own docs -- no longer tanks Site Health
the way it used to (20 pages missing a Twitter card reading as 20 SEO
"warnings" that dragged the score down). See app/audit_classification.py's
module docstring for the full reasoning. In-memory SQLite; no network.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import audit, audit_classification, dataforseo_onpage, models


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


def _make_page(db, **kwargs):
    project = models.Project(name="t", base_url="https://example.com")
    db.add(project)
    db.flush()
    page = models.Page(project_id=project.id, url="https://example.com/", source="dataforseo", **kwargs)
    db.add(page)
    db.flush()
    return page


def _store_issues(db, page, issue_dicts):
    for d in issue_dicts:
        db.add(models.Issue(project_id=page.project_id, page_id=page.id, **d))
    db.flush()


# ── classify() ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("category,rule", [
    ("canonical", "missing"),
    ("opengraph", "missing"),
    ("twitter", "missing"),
    ("content", "thin"),
])
def test_informational_rules_are_not_score_eligible(category, rule):
    c = audit_classification.classify(category, rule)
    assert c["score_eligible"] is False
    assert c["impact"] == "low"


def test_meta_description_missing_is_an_opportunity_not_an_error():
    c = audit_classification.classify("meta_description", "missing")
    assert c["score_eligible"] is False


def test_real_defects_stay_score_eligible():
    for category, rule in [("title", "missing"), ("h1", "missing"), ("security", "no_ssl")]:
        c = audit_classification.classify(category, rule)
        assert c["score_eligible"] is True


def test_unmapped_rule_falls_back_instead_of_raising():
    c = audit_classification.classify("some_future_provider_check", "weird_rule")
    assert c == {"impact": "medium", "score_eligible": True, "classification_version": audit_classification.CLASSIFICATION_VERSION}


# ── provider _issue() helpers stamp the classification ──────────────────

def test_dataforseo_issues_from_item_stamps_classification():
    item = {
        "checks": {"canonical": False, "no_description": True},
        "meta": {},
        "social_media_tags": {},
    }
    issues = {i["category"]: i for i in dataforseo_onpage.issues_from_item(item)}
    assert issues["canonical"]["score_eligible"] is False
    assert issues["meta_description"]["score_eligible"] is False
    assert issues["meta_description"]["severity"] == "warning"  # not "error" -- see audit review 2026-09-29
    assert issues["opengraph"]["score_eligible"] is False
    assert issues["twitter"]["score_eligible"] is False


# ── the actual regression this refactor fixes ────────────────────────────

def test_missing_canonical_og_twitter_do_not_move_health_score(db):
    """The exact scenario from the original bug report: a page with no
    canonical, no OpenGraph, no Twitter card, and no meta description --
    all optional per Google's docs -- should score 100, not be treated as
    4 separate defects."""
    page = _make_page(db)
    issues = audit.audit_page(models.Page(
        title="A perfectly good title that is long enough",
        meta_description="",  # triggers meta_description/missing
        h1=["A Perfectly Good Heading"], h2=["A Subheading"],
        heading_structure=[{"tag": "h1"}, {"tag": "h2"}],
        canonical="",  # triggers canonical/missing
        og_title="", og_description="",  # triggers opengraph/missing
        twitter_card="",  # triggers twitter/missing
        custom_content="word " * 500,  # not thin
        image_alts=[],
        domain_schema=[{"@type": "Organization"}], page_schemas=[],
        lang="en",
    ))
    _store_issues(db, page, issues)
    stored = db.query(models.Issue).filter(models.Issue.page_id == page.id).all()
    assert {i.category for i in stored} == {"canonical", "opengraph", "twitter", "meta_description"}
    assert audit_classification.project_health_score(stored) == 100
    assert audit.page_score(stored) == 100


def test_20_pages_of_social_metadata_noise_does_not_tank_the_score():
    """Reconstructs the screenshot that started this refactor: 20 pages each
    missing OpenGraph + Twitter Card. Old formula: 100 - min(40, 40) = 60.
    New formula: those 40 findings aren't score_eligible, so health stays 100."""
    issues = []
    for _ in range(20):
        issues.append(dataforseo_onpage._issue("opengraph", "missing", "warning", "OpenGraph missing."))
        issues.append(dataforseo_onpage._issue("twitter", "missing", "warning", "Twitter card missing."))
    assert len(issues) == 40
    assert audit_classification.project_health_score(issues) == 100


def test_a_real_defect_still_moves_the_score():
    """Sanity check the other direction -- this refactor shouldn't make the
    score blind to actual problems."""
    issues = [dataforseo_onpage._issue("title", "missing", "error", "Page title is missing.")]
    assert audit_classification.project_health_score(issues) == 96  # 100 - 4


def test_opportunities_are_not_silently_dropped_from_the_issue_list():
    """Per explicit product decision: informational findings must stay
    visible to the user (issue list, CSV export, category breakdown) --
    only the SCORE ignores them, nothing hides them."""
    issues = [dataforseo_onpage._issue("canonical", "missing", "warning", "Canonical link is missing.")]
    assert len(issues) == 1
    assert issues[0]["category"] == "canonical"
    assert issues[0]["score_eligible"] is False
