"""
When the AI provider can't be used (missing key, no credits, rate limit, outage)
the user must see WHY and the failure must be logged -- not a blank "HTTP 500".
And a failed generation must never destroy the suggestions already waiting for
review. Provider is stubbed; in-memory SQLite.
"""
import logging
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import audit_classification, models
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
    page = models.Page(project_id=project.id, url="https://example.com/a/", source="dataforseo",
                       title="A page title that is long enough to be fine", meta_description="d")
    s.add(page); s.flush()
    issue = models.Issue(project_id=project.id, page_id=page.id, category="meta_description", rule="duplicate",
                         severity="warning", message="dup", **audit_classification.classify("meta_description", "duplicate"))
    s.add(issue); s.flush()
    s.add(models.Suggestion(project_id=project.id, page_id=page.id, issue_id=issue.id, content="Old pending idea",
                            content_hash="pending-1", rank=1, status="pending"))
    s.add(models.Suggestion(project_id=project.id, page_id=page.id, issue_id=issue.id, content="Decided idea",
                            content_hash="decided-1", rank=2, status="accepted"))
    s.commit()
    params = {"project_id": project.id, "page_id": page.id, "issue_id": issue.id}
    s.close()

    def override():
        sess = Session()
        try:
            yield sess
        finally:
            sess.close()

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app), params, Session
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def _statuses(Session, issue_id):
    with Session() as s:
        return sorted((x.content, x.status) for x in s.query(models.Suggestion).filter_by(issue_id=issue_id))


def test_missing_api_key_gives_a_clear_502_not_a_blank_500(env, caplog):
    client, params, _ = env
    with patch("app.ai_provider.generate_suggestions", side_effect=KeyError("ANTHROPIC_API_KEY")), \
         caplog.at_level(logging.WARNING, logger="suggestions"):
        r = client.post("/api/suggest", params=params)
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert "not configured" in detail and "ANTHROPIC_API_KEY" in detail
    assert any("suggestions.provider_failed" in rec.getMessage() for rec in caplog.records)


def test_provider_error_message_reaches_the_user_and_the_log_has_no_secret(env, caplog):
    client, params, _ = env
    err = RuntimeError("Your credit balance is too low to access the Anthropic API")
    with patch("app.ai_provider.generate_suggestions", side_effect=err), caplog.at_level(logging.WARNING, logger="suggestions"):
        r = client.post("/api/suggest", params=params)
    assert r.status_code == 502 and "credit balance is too low" in r.json()["detail"]
    logged = " ".join(rec.getMessage() for rec in caplog.records)
    assert "provider_failed" in logged and "RuntimeError" in logged
    assert "sk-" not in logged and "x-api-key" not in logged


def test_a_failed_generation_keeps_the_pending_and_decided_suggestions(env):
    client, params, Session = env
    before = _statuses(Session, params["issue_id"])
    with patch("app.ai_provider.generate_suggestions", side_effect=RuntimeError("boom")):
        assert client.post("/api/suggest", params=params).status_code == 502
    assert _statuses(Session, params["issue_id"]) == before     # nothing was destroyed


def test_an_empty_answer_also_keeps_what_is_waiting(env):
    client, params, Session = env
    before = _statuses(Session, params["issue_id"])
    with patch("app.ai_provider.generate_suggestions", return_value=[]):
        assert client.post("/api/suggest", params=params).status_code == 200
    assert _statuses(Session, params["issue_id"]) == before


def test_success_replaces_pending_but_keeps_decided(env):
    client, params, Session = env
    with patch("app.ai_provider.generate_suggestions", return_value=["A brand new meta description idea for this page"]):
        r = client.post("/api/suggest", params=params)
    assert r.status_code == 200 and len(r.json()["suggestions"]) == 1
    after = _statuses(Session, params["issue_id"])
    assert ("Old pending idea", "pending") not in after
    assert ("Decided idea", "accepted") in after
    assert ("A brand new meta description idea for this page", "pending") in after
