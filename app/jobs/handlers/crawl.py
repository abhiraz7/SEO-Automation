"""
Job handler for job_type='crawl'. Wraps the existing crawler.crawl_site() +
routes.crawl.upsert_page() so a crawl can run from a Job row (scheduler tick
or manual "run now") instead of only from a direct HTTP POST.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ... import models
from ...crawler import crawl_site
from ...routes.crawl import upsert_page


def _utcnow():
    return datetime.now(timezone.utc)


def run_crawl_job(db: Session, job: models.Job) -> None:
    """Marks the job running, crawls the project's base_url, upserts every
    page result, marks completed/failed. Never raises -- a job handler that
    raises would crash whichever loop is driving it (scheduler tick or the
    test-crawl endpoint), so every failure is recorded on the job row
    instead.

    NOTE: crawler.crawl_site() only accepts base_url + max_pages. Most
    Crawler Settings drawer fields (user agent, delay, timeout, exclude
    patterns, worker tuning) are persisted on the project's Schedule row
    (Task 2.2) but crawler.py has no parameters for them yet -- this handler
    only honors max_pages from the job payload, so it doesn't silently
    pretend to apply settings the crawl engine can't actually use.
    """
    job.status = "running"
    job.started_at = _utcnow()
    job.attempts = (job.attempts or 0) + 1
    db.commit()

    try:
        project = db.get(models.Project, job.project_id)
        if not project:
            raise ValueError(f"Project {job.project_id} not found")

        max_pages = (job.payload or {}).get("max_pages", 25)
        results = crawl_site(project.base_url, max_pages=max_pages)

        pages_ok = 0
        pages_error = 0
        for data in results:
            upsert_page(db, job.project_id, data)
            if data.get("error"):
                pages_error += 1
            else:
                pages_ok += 1

        job.status = "completed"
        job.result_summary = {
            "pages_crawled": pages_ok,
            "pages_errored": pages_error,
            "total": len(results),
        }

        # Auto-audit (Task 5.3): a scheduled/job-driven crawl now produces a
        # fresh audit with zero clicks, matching what a human gets today by
        # clicking Crawl then Run Audit manually. Only for job-driven crawls
        # (scheduler ticks, the test-crawl endpoint) -- the direct
        # POST /projects/{id}/crawl button still leaves "Run Audit" as a
        # separate manual action, unchanged.
        db.add(models.Job(project_id=job.project_id, job_type="audit", status="queued"))
    except Exception as e:
        job.status = "failed"
        job.error = str(e)
    finally:
        job.finished_at = _utcnow()
        db.commit()
