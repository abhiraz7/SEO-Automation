"""
Job handler for job_type='keyword_refresh' (Task 4.2). Re-fetches volume/
difficulty/intent for every TrackedKeyword in every KeywordWorkspace linked
to the job's project, via the same keyword_provider.get_keyword_overview()
used by manual tracking -- honest ok/no_data/error handling carries over
unchanged: a snapshot is only written on status=='ok', so a failed/no-data
fetch writes nothing rather than polluting compute_trend() with a fake
zero-value row (same rule as the manual track endpoint).

The Keyword Refresh widget on the Keyword Research page (Task 4.2) defaults
its interval dropdown to Weekly, matching the >=7-day gap compute_trend()
needs to move a keyword's Trend column off "Pending" -- but nothing is
auto-scheduled; a user must explicitly enable it (same as Rank Tracking),
since scheduling recurring paid-API calls without consent isn't something
this job does on its own.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ... import models
from ...keyword_provider import get_keyword_overview


def _utcnow():
    return datetime.now(timezone.utc)


def run_keyword_refresh_job(db: Session, job: models.Job) -> None:
    job.status = "running"
    job.started_at = _utcnow()
    job.attempts = (job.attempts or 0) + 1
    db.commit()

    try:
        project = db.get(models.Project, job.project_id)
        if not project:
            raise ValueError(f"Project {job.project_id} not found")

        workspaces = db.query(models.KeywordWorkspace).filter(
            models.KeywordWorkspace.project_id == job.project_id
        ).all()
        tracked = [
            kw for ws in workspaces
            for kw in db.query(models.TrackedKeyword).filter(models.TrackedKeyword.workspace_id == ws.id).all()
        ]

        refreshed, no_data, errored = 0, 0, 0
        for kw in tracked:
            try:
                normalized = get_keyword_overview(kw.keyword, kw.location)
            except Exception:
                errored += 1
                continue

            if normalized.status == "error":
                errored += 1
                continue
            if normalized.status == "no_data":
                no_data += 1
                continue

            # Carry forward position from the latest prior snapshot -- this
            # job doesn't check rank (that's rank_check's job), so a fresh
            # volume/difficulty snapshot shouldn't blank out the last known
            # position in _latest_snapshot_view.
            prior = max(kw.snapshots, key=lambda s: s.fetched_at) if kw.snapshots else None
            db.add(models.KeywordSnapshot(
                tracked_keyword_id=kw.id,
                volume=normalized.volume,
                difficulty=normalized.difficulty,
                intent=normalized.intent,
                position=prior.position if prior else None,
                trend_points=",".join(f"{p:g}" for p in normalized.trend_points) if normalized.trend_points else None,
                source=normalized.source,
                fetched_at=_utcnow(),
            ))
            refreshed += 1
        db.commit()

        job.status = "completed"
        job.result_summary = {"keywords_refreshed": refreshed, "no_data": no_data, "errors": errored}
    except Exception as e:
        job.status = "failed"
        job.error = str(e)
    finally:
        job.finished_at = _utcnow()
        db.commit()
