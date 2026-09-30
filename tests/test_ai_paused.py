"""AI suggestions are paused for content/thin ("Low text-to-page-size ratio"):
the server must refuse to generate (no provider call at all), the page data
must hide stored suggestions and carry the reason, and other categories must
be unaffected. See app/issue_copy.py PAUSED_AI."""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import audit_classification, issue_copy, models
from app.database import get_db
from app.main import app


@pytest.fixture
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    project = models.Project(name="t", base_url="https://example.com")
    s.add(project); s.flush()
    page = models.Page(project_id=project.id, url="https://example.com/subject/hindi/", source="dataforseo",
                       title="Hindi Archives - Example Site For Notes", meta_description="d")
    s.add(page); s.flush()
    ids = {}
    for key, (cat, rule, sev) in {"thin": ("content", "thin", "warning"), "title": ("title", "too_long", "warning")}.items():
        issue = models.Issue(project_id=project.id, page_id=page.id, category=cat, rule=rule, severity=sev,
                             message=f"{cat} {rule}", **audit_classification.classify(cat, rule))
        s.add(issue); s.flush()
        ids[key] = issue.id
    # a suggestion already stored for the paused finding: must stay in the DB, just not shown
    s.add(models.Suggestion(project_id=project.id, page_id=page.id, issue_id=ids["thin"],
                            content="Add 800-1200 words", content_hash="h", rank=1))
    s.commit()
    pid, page_id = project.id, page.id
    s.close()

    def override():
        sess = Session()
        try:
            yield sess
        finally:
            sess.close()

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app), pid, page_id, ids, Session
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_reason_helper():
    assert issue_copy.ai_paused_reason("content", "thin")
    assert issue_copy.ai_paused_reason("title", "too_long") is None


def test_generate_is_refused_and_never_calls_the_provider(env):
    client, pid, page_id, ids, _ = env
    with patch("app.ai_provider.generate_suggestions", side_effect=AssertionError("provider must not be called")):
        resp = client.post("/api/suggest", params={"project_id": pid, "page_id": page_id, "issue_id": ids["thin"]})
    assert resp.status_code == 409
    assert "paused" in resp.json()["detail"].lower()


def test_other_categories_still_generate(env):
    client, pid, page_id, ids, _ = env
    with patch("app.ai_provider.generate_suggestions", return_value=["A shorter, clearer page title"]):
        resp = client.post("/api/suggest", params={"project_id": pid, "page_id": page_id, "issue_id": ids["title"]})
    assert resp.status_code == 200 and len(resp.json()["suggestions"]) == 1


def test_generated_suggestion_response_carries_checks(env):
    """A suggestion produced inside the open modal must arrive with its checks,
    or the badge shows 'Needs review' with nothing behind it."""
    client, pid, page_id, ids, _ = env
    with patch("app.ai_provider.generate_suggestions", return_value=["Hindi Archives: Notes and Study Material"]):
        resp = client.post("/api/suggest", params={"project_id": pid, "page_id": page_id, "issue_id": ids["title"]})
    s = resp.json()["suggestions"][0]
    assert s["checks"] and s["checks_summary"]["total"] == len(s["checks"])
    assert s["checks_summary"]["state"] in {"ok", "partial", "review"}


def test_content_current_value_shows_word_count_not_blank():
    from types import SimpleNamespace
    from app import audit
    page = SimpleNamespace(fit_markdown=None, custom_content=None, word_count=142)
    assert audit.current_value_display(page, "content") == "142 words of visible text (no excerpt stored)"
    assert audit.current_value_display(SimpleNamespace(fit_markdown=None, custom_content=None, word_count=None), "content") == "-Blank-"


def test_page_hides_stored_suggestions_for_paused_finding_but_keeps_them_in_the_db(env):
    client, pid, _, ids, Session = env
    html = client.get(f"/projects/{pid}/onpage").text
    assert "Add 800-1200 words" not in html
    assert '"ai_paused": "AI suggestions are paused' in html
    assert ">Manual</span>" in html
    with Session() as s:
        assert s.query(models.Suggestion).filter_by(issue_id=ids["thin"]).count() == 1
