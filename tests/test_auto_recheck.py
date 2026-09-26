"""
Automatic re-check of a "saved but not showing" deploy.

A page/CDN cache can serve the old value for a while after a correct write, so
one mismatch is not final. These tests cover: delayed jobs waiting in status
'waiting' (never 'queued' -- the dashboard spins on queued jobs), promotion when
due, escalating delays, no wasted checks on rolled-back/superseded revisions,
and the manual Re-check bringing a pending one forward instead of stacking.
No network: DataForSEO is mocked; the database is in-memory SQLite.
"""
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import dataforseo_onpage, deploy_status, models, scheduler
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


def _seed(db):
    project = models.Project(name="P", base_url="https://site.com")
    db.add(project)
    db.flush()
    page = models.Page(project_id=project.id, url="https://site.com/a", meta_description="old")
    db.add(page)
    db.flush()
    issue = models.Issue(project_id=project.id, page_id=page.id, category="meta_description", rule="missing", message="m")
    db.add(issue)
    db.flush()
    sug = models.Suggestion(project_id=project.id, page_id=page.id, issue_id=issue.id, content="c", status="deployed")
    db.add(sug)
    db.flush()
    rev = models.SuggestionRevision(
        suggestion_id=sug.id, project_id=project.id, field_name="meta_description",
        before_value="old", after_value="New description", wp_post_id=1, deployed_via="yoast_set_meta",
    )
    db.add(rev)
    db.commit()
    return project, page, sug, rev


def _job(db, project_id, revision_id, attempt=1, status="queued", not_before=None):
    payload = {"revision_id": revision_id, "attempt": attempt}
    if not_before:
        payload["not_before"] = not_before
    job = models.Job(project_id=project_id, job_type="verify_deploy", status=status, payload=payload)
    db.add(job)
    db.commit()
    return job


def _run(db, job, live_description):
    item = {"meta": {"description": live_description}}
    with mock.patch.object(dataforseo_onpage, "instant_page_check", return_value=item) as check:
        verify_deploy.run_verify_deploy_job(db, job)
    return check


def _waiting(db):
    return db.query(models.Job).filter(models.Job.status == "waiting").all()


# ── due-ness and promotion ───────────────────────────────────────────────

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("payload,expected", [
    (None, True),
    ({}, True),
    ({"not_before": (NOW + timedelta(minutes=1)).isoformat()}, False),
    ({"not_before": (NOW - timedelta(minutes=1)).isoformat()}, True),
    ({"not_before": NOW.isoformat()}, True),
    ({"not_before": "2026-09-27T11:00:00"}, True),   # naive time is read as UTC
    ({"not_before": "2026-09-27T13:00:00"}, False),
    ({"not_before": "garbage"}, True),                # unparseable: run, don't starve
])
def test_job_is_due(payload, expected):
    assert scheduler._job_is_due(models.Job(payload=payload), NOW) is expected


def test_only_due_waiting_jobs_in_the_lane_are_promoted(db):
    project, _, _, rev = _seed(db)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    due = _job(db, project.id, rev.id, status="waiting", not_before=past)
    later = _job(db, project.id, rev.id, status="waiting", not_before=future)
    other_lane = models.Job(project_id=project.id, job_type="crawl", status="waiting", payload={"not_before": past})
    db.add(other_lane)
    db.commit()

    promoted = scheduler._promote_due_waiting_jobs(db, {"verify_deploy"})

    db.refresh(due); db.refresh(later); db.refresh(other_lane)
    assert promoted == 1
    assert due.status == "queued"
    assert later.status == "waiting"
    assert other_lane.status == "waiting"  # not this lane's job


# ── the handler schedules escalating re-checks ───────────────────────────

@pytest.mark.parametrize("attempt,delay_minutes", [(1, 3), (2, 10), (3, 30)])
def test_mismatch_schedules_the_next_check_with_an_escalating_delay(db, attempt, delay_minutes):
    project, _, _, rev = _seed(db)
    job = _job(db, project.id, rev.id, attempt=attempt)
    before = datetime.now(timezone.utc)

    _run(db, job, "Something else the cache still serves")

    db.refresh(rev); db.refresh(job)
    assert rev.verify_status == "mismatch"
    assert job.status == "completed" and job.result_summary["recheck_scheduled"] is True
    (nxt,) = _waiting(db)
    assert nxt.payload["revision_id"] == rev.id and nxt.payload["attempt"] == attempt + 1
    due = datetime.fromisoformat(nxt.payload["not_before"])
    assert timedelta(minutes=delay_minutes) - timedelta(seconds=30) <= due - before <= timedelta(minutes=delay_minutes, seconds=30)


def test_parked_recheck_is_never_status_queued(db):
    """The dashboard treats any queued job as 'still fetching' and spins."""
    project, _, _, rev = _seed(db)
    _run(db, _job(db, project.id, rev.id), "still old")
    parked = db.query(models.Job).filter(models.Job.job_type == "verify_deploy", models.Job.id != 1).all()
    assert parked and all(j.status == "waiting" for j in parked)
    assert db.query(models.Job).filter(models.Job.status == "queued").count() == 0


def test_last_attempt_stops_and_the_mismatch_stands(db):
    project, _, _, rev = _seed(db)
    job = _job(db, project.id, rev.id, attempt=len(verify_deploy.RECHECK_DELAYS_MINUTES) + 1)
    _run(db, job, "still old")
    db.refresh(rev); db.refresh(job)
    assert rev.verify_status == "mismatch"
    assert job.result_summary["recheck_scheduled"] is False
    assert _waiting(db) == []


def test_verified_needs_no_recheck_and_updates_our_page_copy(db):
    project, page, _, rev = _seed(db)
    job = _job(db, project.id, rev.id)
    _run(db, job, "New description")
    db.refresh(rev); db.refresh(page)
    assert rev.verify_status == "verified" and page.meta_description == "New description"
    assert _waiting(db) == []


def test_a_failed_check_is_retried_too(db):
    project, _, _, rev = _seed(db)
    job = _job(db, project.id, rev.id)
    with mock.patch.object(dataforseo_onpage, "instant_page_check", return_value={"error": "timeout"}):
        verify_deploy.run_verify_deploy_job(db, job)
    db.refresh(rev)
    assert rev.verify_status == "error" and len(_waiting(db)) == 1


def test_rolled_back_revision_is_skipped_without_calling_dataforseo(db):
    project, _, _, rev = _seed(db)
    rev.rolled_back_at = datetime.now(timezone.utc)
    db.commit()
    job = _job(db, project.id, rev.id)
    check = _run(db, job, "anything")
    db.refresh(job); db.refresh(rev)
    check.assert_not_called()
    assert job.status == "completed" and "skipped" in job.result_summary
    assert rev.verify_status == "pending" and _waiting(db) == []


def test_superseded_revision_is_skipped(db):
    """A redeploy adds a newer revision without rolling the old one back; the page
    now shows the newer value, so checking the old one would be a false mismatch."""
    project, _, sug, old = _seed(db)
    newer = models.SuggestionRevision(
        suggestion_id=sug.id, project_id=project.id, field_name="meta_description",
        before_value="New description", after_value="Newer description", wp_post_id=1, deployed_via="yoast_set_meta",
    )
    db.add(newer)
    db.commit()
    check = _run(db, _job(db, project.id, old.id), "Newer description")
    check.assert_not_called()
    db.refresh(old)
    assert old.verify_status == "pending" and _waiting(db) == []


def test_no_duplicate_when_a_check_for_the_revision_is_already_pending(db):
    project, _, _, rev = _seed(db)
    _job(db, project.id, rev.id, attempt=3, status="waiting",
         not_before=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat())
    _run(db, _job(db, project.id, rev.id), "still old")
    assert len(_waiting(db)) == 1


# ── manual Re-check ──────────────────────────────────────────────────────

def _verify_jobs(db):
    return db.query(models.Job).filter(models.Job.job_type == "verify_deploy").all()


def test_manual_recheck_brings_a_waiting_check_forward_instead_of_stacking(db):
    project, _, _, rev = _seed(db)
    waiting = _job(db, project.id, rev.id, attempt=2, status="waiting",
                   not_before=(datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat())
    wp_routes.reverify_revision(rev.id, db)
    db.refresh(waiting)
    assert waiting.status == "queued"
    assert len(_verify_jobs(db)) == 1


def test_manual_recheck_leaves_an_active_check_alone(db):
    project, _, _, rev = _seed(db)
    _job(db, project.id, rev.id, status="queued")
    wp_routes.reverify_revision(rev.id, db)
    assert len(_verify_jobs(db)) == 1


def test_manual_recheck_creates_one_job_when_none_exist(db):
    project, _, _, rev = _seed(db)
    wp_routes.reverify_revision(rev.id, db)
    wp_routes.reverify_revision(rev.id, db)  # second click must not add another
    assert len(_verify_jobs(db)) == 1
    db.refresh(rev)
    assert rev.verify_status == "pending"


def test_manual_recheck_of_a_rolled_back_revision_is_refused(db):
    project, _, _, rev = _seed(db)
    rev.rolled_back_at = datetime.now(timezone.utc)
    db.commit()
    with pytest.raises(HTTPException) as e:
        wp_routes.reverify_revision(rev.id, db)
    assert e.value.status_code == 409


# ── what the UI is told ──────────────────────────────────────────────────

def test_ui_is_told_a_recheck_is_pending(db):
    project, _, sug, rev = _seed(db)
    rev.verify_status = "mismatch"
    db.commit()
    _job(db, project.id, rev.id, attempt=2, status="waiting",
         not_before=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat())

    fields = deploy_status.suggestion_live_fields(deploy_status.live_status_for_suggestions(db, [sug.id]), sug)

    assert fields["live_status"] == "not_showing" and fields["live_rechecking"] is True


def test_ui_is_not_told_rechecking_when_nothing_is_waiting(db):
    _, _, sug, rev = _seed(db)
    rev.verify_status = "mismatch"
    db.commit()
    fields = deploy_status.suggestion_live_fields(deploy_status.live_status_for_suggestions(db, [sug.id]), sug)
    assert fields["live_rechecking"] is False
