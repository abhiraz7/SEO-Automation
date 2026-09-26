"""
deploy_status + verify_deploy + reverify_revision tests. In-memory SQLite only
(never the real seo_automation.db); DataForSEO is patched, no network.
"""
from datetime import datetime, timezone
from unittest import mock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import dataforseo_onpage, deploy_status, models
from app.jobs.handlers import verify_deploy
from app.routes import wordpress as wp_routes


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


def _seed(db, category="title", suggestion_status="deployed", page_title="Old title"):
    project = models.Project(name="P", base_url="https://site.com")
    db.add(project)
    db.flush()
    page = models.Page(project_id=project.id, url="https://site.com/a", title=page_title)
    db.add(page)
    db.flush()
    issue = models.Issue(project_id=project.id, page_id=page.id, category=category, rule="missing", message="m")
    db.add(issue)
    db.flush()
    sug = models.Suggestion(
        project_id=project.id, page_id=page.id, issue_id=issue.id,
        content="c", status=suggestion_status,
    )
    db.add(sug)
    db.flush()
    db.commit()
    return project, page, sug


def _rev(db, sug, field="title", after="New title", verify="pending", rolled_back=False):
    r = models.SuggestionRevision(
        suggestion_id=sug.id, project_id=sug.project_id, field_name=field,
        before_value="Old", after_value=after, wp_post_id=1, deployed_via="yoast_set_meta",
        verify_status=verify,
        rolled_back_at=datetime.now(timezone.utc) if rolled_back else None,
    )
    db.add(r)
    db.commit()
    return r


# 1 ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("pending", "checking"), ("verified", "live"), ("mismatch", "not_showing"),
    ("error", "unverified"), (None, "checking"), ("weird", "checking"),
])
def test_live_status_from_verify(raw, expected):
    assert deploy_status.live_status_from_verify(raw) == expected


# 2 ---------------------------------------------------------------------------

def test_live_status_for_suggestions_newest_wins(db):
    _, _, sug = _seed(db)
    _rev(db, sug, verify="mismatch")
    newer = _rev(db, sug, verify="verified")
    out = deploy_status.live_status_for_suggestions(db, [sug.id])
    assert out[sug.id]["live_status"] == "live"
    assert out[sug.id]["revision_id"] == newer.id


def test_live_status_for_suggestions_ignores_rolled_back(db):
    _, _, sug = _seed(db)
    older = _rev(db, sug, verify="mismatch")
    _rev(db, sug, verify="verified", rolled_back=True)
    out = deploy_status.live_status_for_suggestions(db, [sug.id])
    assert out[sug.id]["revision_id"] == older.id
    assert out[sug.id]["live_status"] == "not_showing"


def test_live_status_for_suggestions_empty_and_missing(db):
    _, _, sug = _seed(db)
    assert deploy_status.live_status_for_suggestions(db, []) == {}
    assert deploy_status.live_status_for_suggestions(db, [sug.id]) == {}


# 3 ---------------------------------------------------------------------------

def test_suggestion_live_fields_not_deployed_is_empty(db):
    _, _, sug = _seed(db, suggestion_status="accepted")
    assert deploy_status.suggestion_live_fields({}, sug) == {}


def test_suggestion_live_fields_deployed_without_revision_is_unverified(db):
    _, _, sug = _seed(db)
    out = deploy_status.suggestion_live_fields({}, sug)
    assert out["live_status"] == "unverified"
    assert out["live_revision_id"] is None


def test_suggestion_live_fields_deployed_verified_is_live(db):
    _, _, sug = _seed(db)
    rev = _rev(db, sug, verify="verified")
    status_map = deploy_status.live_status_for_suggestions(db, [sug.id])
    out = deploy_status.suggestion_live_fields(status_map, sug)
    assert out["live_status"] == "live"
    assert out["live_revision_id"] == rev.id


# 4 ---------------------------------------------------------------------------

def test_apply_verified_value_sets_columns(db):
    _, page, _ = _seed(db)
    deploy_status.apply_verified_value_to_page(db, page.id, "title", "T")
    deploy_status.apply_verified_value_to_page(db, page.id, "meta_description", "D")
    assert page.title == "T"
    assert page.meta_description == "D"


def test_apply_verified_value_h1_is_single_item_list(db):
    _, page, _ = _seed(db)
    page.h1 = ["a", "b"]
    db.commit()
    deploy_status.apply_verified_value_to_page(db, page.id, "h1", "Only")
    assert page.h1 == ["Only"]


def test_apply_verified_value_unknown_field_and_missing_page_are_noops(db):
    _, page, _ = _seed(db)
    deploy_status.apply_verified_value_to_page(db, page.id, "bogus", "x")
    assert page.title == "Old title"
    deploy_status.apply_verified_value_to_page(db, 99999, "title", "x")  # must not raise


# 5 ---------------------------------------------------------------------------

def _run_handler(db, project, rev, item):
    job = models.Job(project_id=project.id, job_type="verify_deploy", payload={"revision_id": rev.id})
    db.add(job)
    db.commit()
    with mock.patch.object(dataforseo_onpage, "instant_page_check", return_value=item):
        verify_deploy.run_verify_deploy_job(db, job)
    db.refresh(rev)
    return job


def test_verify_handler_match_verifies_and_updates_page(db):
    project, page, sug = _seed(db)
    rev = _rev(db, sug, after="New title")
    job = _run_handler(db, project, rev, {"meta": {"title": "  new TITLE "}})
    db.refresh(page)
    assert job.status == "completed"
    assert rev.verify_status == "verified"
    assert page.title == "New title"


def test_verify_handler_mismatch_leaves_page_unchanged(db):
    project, page, sug = _seed(db)
    rev = _rev(db, sug, after="New title")
    _run_handler(db, project, rev, {"meta": {"title": "Something else"}})
    db.refresh(page)
    assert rev.verify_status == "mismatch"
    assert page.title == "Old title"


def test_verify_handler_error_item_leaves_page_unchanged(db):
    project, page, sug = _seed(db)
    rev = _rev(db, sug, after="New title")
    job = _run_handler(db, project, rev, {"error": "unreachable"})
    db.refresh(page)
    assert job.status == "completed"
    assert rev.verify_status == "error"
    assert rev.verify_detail == "unreachable"
    assert page.title == "Old title"


# 6 ---------------------------------------------------------------------------

def _verify_jobs(db):
    return db.query(models.Job).filter(models.Job.job_type == "verify_deploy").all()


def test_reverify_resets_and_queues_one_job(db):
    _, _, sug = _seed(db)
    rev = _rev(db, sug, verify="mismatch")
    wp_routes.reverify_revision(rev.id, db)
    assert rev.verify_status == "pending"
    assert len(_verify_jobs(db)) == 1


def test_reverify_twice_does_not_double_queue(db):
    _, _, sug = _seed(db)
    rev = _rev(db, sug, verify="mismatch")
    wp_routes.reverify_revision(rev.id, db)
    wp_routes.reverify_revision(rev.id, db)
    assert len(_verify_jobs(db)) == 1


def test_reverify_rolled_back_is_409(db):
    _, _, sug = _seed(db)
    rev = _rev(db, sug, rolled_back=True)
    with pytest.raises(HTTPException) as ei:
        wp_routes.reverify_revision(rev.id, db)
    assert ei.value.status_code == 409
    assert _verify_jobs(db) == []


def test_reverify_unknown_is_404(db):
    with pytest.raises(HTTPException) as ei:
        wp_routes.reverify_revision(424242, db)
    assert ei.value.status_code == 404
