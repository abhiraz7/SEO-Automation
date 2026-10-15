"""Display helpers for the On-Page rows: they must derive labels from stored
data only and never invent a value."""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import audit_classification, issue_copy, models
from app.routes.onpage_semrush import measured_length, page_display_name


@pytest.mark.parametrize("url,expected", [
    ("https://example.com/", "Home"),
    ("https://example.com", "Home"),
    ("", "Home"),
    (None, "Home"),
    ("https://example.com/free-notes/indian-polity-notes/", "Indian polity notes"),
    ("https://example.com/about_us.html", "About us"),
    ("https://example.com/diploma/", "Diploma"),
    ("https://example.com/%E0%A4%B9%E0%A4%BF%E0%A4%82%E0%A4%A6%E0%A5%80", "हिंदी"),
])
def test_page_display_name(url, expected):
    assert page_display_name(url) == expected


def test_measured_length_only_for_title_and_meta_description():
    page = SimpleNamespace(title="  Hello world  ", meta_description="abc", canonical="https://x")
    assert measured_length(page, "title") == 11
    assert measured_length(page, "meta_description") == 3
    assert measured_length(page, "canonical") is None
    assert measured_length(page, "h1") is None


def test_measured_length_is_none_for_blank_or_missing_page():
    assert measured_length(SimpleNamespace(title="   ", meta_description=None), "title") is None
    assert measured_length(SimpleNamespace(title="x", meta_description=None), "meta_description") is None
    assert measured_length(None, "title") is None


@pytest.fixture
def duplicate_pages_session():
    """Two dataforseo-sourced pages in one project, sharing both title and
    meta description -- the exact shape DataForSEO's duplicate_title_tag /
    duplicate_meta_tags checks flag, each stored with the generic
    placeholder message dataforseo_onpage.issues_from_item sets (it has no
    cross-page info to do better -- see that module's issues_from_item)."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    project = models.Project(name="t", base_url="https://example.com")
    session.add(project)
    session.flush()

    page_a = models.Page(project_id=project.id, url="https://example.com/a", source="dataforseo",
                          title="Same Title", meta_description="Same description")
    page_b = models.Page(project_id=project.id, url="https://example.com/b", source="dataforseo",
                          title="Same Title", meta_description="Same description")
    session.add_all([page_a, page_b])
    session.flush()

    for page in (page_a, page_b):
        session.add(models.Issue(
            project_id=project.id, page_id=page.id, category="title", rule="duplicate",
            severity="warning", message="Title tag is duplicated on another crawled page.",
            **audit_classification.classify("title", "duplicate"),
        ))
        session.add(models.Issue(
            project_id=project.id, page_id=page.id, category="meta_description", rule="duplicate",
            severity="warning", message="Meta tags are duplicated on another crawled page.",
            **audit_classification.classify("meta_description", "duplicate"),
        ))
    session.commit()

    yield session, project.id, page_a.id, page_b.id
    session.close()
    engine.dispose()


def test_duplicate_meta_description_issue_names_the_other_page(duplicate_pages_session):
    """Regression: the Meta Description fix modal says "Meta description is
    duplicated on another page" but never which page -- confirmed live on a
    real project (kentuckymarijuana.org/terms-conditions/) where the only
    detail line was the generic "Another crawled page uses the same meta
    tags.", giving the user nothing to act on. The fix must name the other
    URL in the issue's detail text."""
    from app.routes.onpage_semrush import _annotate_duplicate_tag_messages

    session, project_id, page_a_id, page_b_id = duplicate_pages_session
    _annotate_duplicate_tag_messages(session, project_id, "dataforseo")

    issue_a = session.query(models.Issue).filter_by(page_id=page_a_id, category="meta_description").first()
    issue_b = session.query(models.Issue).filter_by(page_id=page_b_id, category="meta_description").first()

    assert "https://example.com/b" in issue_a.message
    assert "https://example.com/a" in issue_b.message

    detail = issue_copy.explain("meta_description", "duplicate", issue_a.message)["detail"]
    assert "https://example.com/b" in detail


def test_duplicate_title_issue_names_the_other_page(duplicate_pages_session):
    """Same bug, title category: DataForSEO's duplicate_title_tag check also
    never names the duplicate page."""
    from app.routes.onpage_semrush import _annotate_duplicate_tag_messages

    session, project_id, page_a_id, page_b_id = duplicate_pages_session
    _annotate_duplicate_tag_messages(session, project_id, "dataforseo")

    issue_a = session.query(models.Issue).filter_by(page_id=page_a_id, category="title").first()
    assert "https://example.com/b" in issue_a.message
