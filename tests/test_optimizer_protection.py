"""
The AI Content Optimizer stores its suggestions under Issues whose rule starts with
'opt_'. Those are not audit findings, so every path that deletes or replaces audit
Issues must leave them (and the user's decisions on their suggestions) alone -- and
the legacy 'Generate' must not overwrite them. Each test here fails if its protection
is removed. In-memory SQLite; no network.
"""
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.database import get_db
from app.jobs.handlers import audit as audit_job
from app.main import app
from app.routes import audit as audit_route
from app.routes import onpage_semrush as onpage
from app.routes import suggestions as suggestions_route


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def make_world(db):
    """A project + page with: one AUDIT issue that stays flagged, one AUDIT issue that
    is later resolved, and one OPTIMIZER issue holding a deployed and an accepted suggestion."""
    project = models.Project(name="P", base_url="https://mine.com")
    db.add(project)
    db.flush()
    page = models.Page(project_id=project.id, url="https://mine.com/a", source="dataforseo", title="Old")
    db.add(page)
    db.flush()

    def issue(category, rule, severity="warning"):
        i = models.Issue(project_id=project.id, page_id=page.id, category=category, rule=rule, severity=severity, message=f"{category} {rule}")
        db.add(i)
        db.flush()
        return i

    def suggestion(i, text, status):
        s = models.Suggestion(project_id=project.id, page_id=page.id, issue_id=i.id, content=text, content_hash=text, status=status)
        db.add(s)
        db.flush()
        return s

    still_flagged = issue("title", "too_short")
    resolved = issue("h1", "missing")
    optimizer = issue("title", "opt_improve_title", severity="info")
    s_still = suggestion(still_flagged, "legacy fix", "accepted")
    s_resolved = suggestion(resolved, "old fix", "accepted")
    s_deployed = suggestion(optimizer, "Better title", "deployed")
    s_accepted = suggestion(optimizer, "Another title", "accepted")
    db.commit()
    return dict(project=project, page=page, still_flagged=still_flagged, resolved=resolved, optimizer=optimizer,
                s_still=s_still, s_resolved=s_resolved, s_deployed=s_deployed, s_accepted=s_accepted)


def survivors(db, ids):
    return sorted(i for (i,) in db.query(models.Suggestion.id).filter(models.Suggestion.id.in_(ids)).all())


# ── on-page refresh (the DataForSEO path: Issues the provider no longer flags are deleted) ───

def test_an_onpage_refresh_keeps_optimizer_issues_and_their_decided_suggestions(db):
    w = make_world(db)
    flagged_now = [{"category": "title", "rule": "too_short", "severity": "error", "message": "still too short"}]
    with patch.object(onpage.dataforseo_onpage, "normalize_page", return_value={"url": w["page"].url, "title": "New"}), \
         patch.object(onpage.dataforseo_onpage, "issues_from_item", return_value=flagged_now):
        onpage._store_page_result(db, w["project"], {}, None)

    assert db.get(models.Issue, w["optimizer"].id) is not None
    assert survivors(db, [w["s_deployed"].id, w["s_accepted"].id]) == sorted([w["s_deployed"].id, w["s_accepted"].id])
    assert db.get(models.Suggestion, w["s_deployed"].id).status == "deployed"


def test_an_onpage_refresh_still_reconciles_audit_issues_as_before(db):
    w = make_world(db)
    flagged_now = [{"category": "title", "rule": "too_short", "severity": "error", "message": "still too short"}]
    with patch.object(onpage.dataforseo_onpage, "normalize_page", return_value={"url": w["page"].url, "title": "New"}), \
         patch.object(onpage.dataforseo_onpage, "issues_from_item", return_value=flagged_now):
        onpage._store_page_result(db, w["project"], {}, None)

    kept = db.get(models.Issue, w["still_flagged"].id)
    assert kept is not None and kept.severity == "error" and kept.message == "still too short"      # updated in place, same id
    assert survivors(db, [w["s_still"].id]) == [w["s_still"].id]                                    # its suggestion history is intact
    assert db.get(models.Issue, w["resolved"].id) is None                                            # no longer flagged: removed, as before
    assert survivors(db, [w["s_resolved"].id]) == []                                                 # ... with its suggestion (the existing cascade)


# ── the dormant audit route and the audit job (bulk-replace every issue in the project) ─────

def test_a_full_audit_replaces_audit_issues_but_not_optimizer_issues(db):
    w = make_world(db)
    audit_route._persist_issues(db, w["project"].id, {w["page"].id: [
        {"category": "meta_description", "rule": "missing", "severity": "error", "message": "no meta"}]})

    assert db.get(models.Issue, w["optimizer"].id) is not None
    assert survivors(db, [w["s_deployed"].id, w["s_accepted"].id]) == sorted([w["s_deployed"].id, w["s_accepted"].id])
    rules = sorted(i.rule for i in db.query(models.Issue).all())
    assert rules == ["missing", "opt_improve_title"]                        # the fresh audit issue + the optimizer's; the old audit issues are gone


def test_the_audit_job_that_follows_every_crawl_also_leaves_optimizer_issues_alone(db):
    w = make_world(db)
    job = models.Job(project_id=w["project"].id, job_type="audit", payload={})
    db.add(job)
    db.commit()
    fresh = {w["page"].id: [{"category": "canonical", "rule": "missing", "severity": "warning", "message": "no canonical"}]}
    with patch.object(audit_job.audit_engine, "run_audit", return_value={}), \
         patch.object(audit_job.audit_engine, "run_security_audit", return_value={}), \
         patch.object(audit_job.audit_engine, "merge_issue_dicts", return_value=fresh):
        audit_job.run_audit_job(db, job)

    assert job.status == "completed"
    assert db.get(models.Issue, w["optimizer"].id) is not None
    assert survivors(db, [w["s_deployed"].id, w["s_accepted"].id]) == sorted([w["s_deployed"].id, w["s_accepted"].id])
    assert sorted(i.rule for i in db.query(models.Issue).all()) == ["missing", "opt_improve_title"]


def test_the_prefix_match_is_exact_an_audit_rule_that_merely_starts_with_opt_is_still_replaced(db):
    """'_' is a LIKE wildcard: without autoescape, 'opt_%' would also match 'optimized_title'."""
    w = make_world(db)
    lookalike = models.Issue(project_id=w["project"].id, page_id=w["page"].id, category="title", rule="optimized_title", severity="warning", message="x")
    db.add(lookalike)
    db.commit()
    lookalike_id = lookalike.id
    audit_route._persist_issues(db, w["project"].id, {})
    assert db.get(models.Issue, lookalike_id) is None
    assert db.get(models.Issue, w["optimizer"].id) is not None


def test_is_optimizer_rule():
    assert models.is_optimizer_rule("opt_add_section") and models.is_optimizer_rule("opt_")
    assert not models.is_optimizer_rule("optimized_title") and not models.is_optimizer_rule("missing")
    assert not models.is_optimizer_rule(None) and not models.is_optimizer_rule("")


# ── the legacy 'Generate' must not touch optimizer suggestions ──────────────

def test_legacy_generate_refuses_an_optimizer_issue_and_changes_nothing(db):
    w = make_world(db)
    pending = models.Suggestion(project_id=w["project"].id, page_id=w["page"].id, issue_id=w["optimizer"].id,
                                content="Undecided title", content_hash="undecided", status="pending")
    db.add(pending)
    db.commit()
    with patch.object(suggestions_route.ai_provider, "generate_suggestions") as ai:
        with pytest.raises(HTTPException) as exc:
            suggestions_route._generate_and_store(db, w["project"].id, w["page"].id, w["optimizer"].id)
    assert exc.value.status_code == 409 and "AI Content Optimizer" in exc.value.detail
    ai.assert_not_called()
    assert db.query(models.Suggestion).filter_by(issue_id=w["optimizer"].id).count() == 3      # the pending one was NOT deleted


def test_legacy_generate_still_works_for_a_normal_audit_issue(db):
    w = make_world(db)
    with patch.object(suggestions_route.context_builder, "build_page_understanding", return_value=None), \
         patch.object(suggestions_route.prompt_builder, "build_suggestion_context", return_value={}), \
         patch.object(suggestions_route.ai_provider, "generate_suggestions", return_value=["Alpha title", "Beta title"]) as ai:
        rows = suggestions_route._generate_and_store(db, w["project"].id, w["page"].id, w["still_flagged"].id)
    ai.assert_called_once()
    assert [r.content for r in rows] == ["Alpha title", "Beta title"]
    assert db.get(models.Suggestion, w["s_still"].id).status == "accepted"                 # decided rows still survive regeneration


def test_the_json_generate_endpoint_returns_409_for_an_optimizer_issue(db):
    w = make_world(db)

    def override():
        yield db

    app.dependency_overrides[get_db] = override
    try:
        r = TestClient(app).post(f"/api/suggest?project_id={w['project'].id}&page_id={w['page'].id}&issue_id={w['optimizer'].id}")
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert r.status_code == 409 and "AI Content Optimizer" in r.json()["detail"]
