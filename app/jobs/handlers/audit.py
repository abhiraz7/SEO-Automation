"""
Job handler for job_type='audit' (Task 5.3). Wraps the existing audit engine
(app/audit.py::run_audit) the same way POST /projects/{id}/audit does --
replaces the project's Issue rows with a fresh audit of its current pages.

Dispatched automatically at the end of a completed crawl job (see
handlers/crawl.py) so a scheduled crawl produces a fresh audit with zero
clicks, matching what happens today when a human clicks Crawl then Run
Audit manually.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ... import models
from ... import audit as audit_engine


def _utcnow():
    return datetime.now(timezone.utc)


def run_audit_job(db: Session, job: models.Job) -> None:
    job.status = "running"
    job.started_at = _utcnow()
    job.attempts = (job.attempts or 0) + 1
    db.commit()

    try:
        project = db.get(models.Project, job.project_id)
        if not project:
            raise ValueError(f"Project {job.project_id} not found")

        pages = db.query(models.Page).filter(models.Page.project_id == job.project_id).all()
        issues_by_page = audit_engine.merge_issue_dicts(
            audit_engine.run_audit(pages),
            audit_engine.run_security_audit(project.base_url, pages),
        )

        # Same replace-all-issues approach as routes/audit.py::_persist_issues,
        # inlined here rather than imported to avoid a jobs -> routes
        # dependency (the crawl handler already sets this precedent -- see
        # its own docstring note about upsert_page living in routes/crawl.py;
        # unlike that case, duplicating this one short loop is cheaper than
        # adding a new cross-layer import).
        db.query(models.Issue).filter(models.Issue.project_id == job.project_id).delete()
        total_issues = 0
        for page_id, issues in issues_by_page.items():
            for issue in issues:
                db.add(models.Issue(project_id=job.project_id, page_id=page_id, **issue))
                total_issues += 1
        db.commit()

        job.status = "completed"
        job.result_summary = {"pages_audited": len(pages), "issues_found": total_issues}
    except Exception as e:
        db.rollback()
        job.status = "failed"
        job.error = str(e)
    finally:
        job.finished_at = _utcnow()
        db.commit()
