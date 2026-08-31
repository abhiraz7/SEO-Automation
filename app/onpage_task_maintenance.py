"""Shared staleness handling for OnPageTask rows (DataForSEO site-audit
crawls, dataforseo_task_id-backed -- DataForSEO runs the crawl on their
end, we just poll).

Previously this logic lived only inline in routes/onpage_semrush.py's
start_site_audit, so it only ever ran REACTIVELY -- when a user tried to
start a new crawl on a project that already had one 'posted'. If nobody
revisited that project's page, a task DataForSEO silently dropped could
sit as 'posted' forever, blocking every future crawl with no visible
signal besides a stale "Auto-checking..." spinner. Real incident: a task
posted 2026-08-24 was still 'posted' on 2026-08-31 -- see AgentLog.

Extracted here so the same check can also run PROACTIVELY from a
scheduler tick (app/scheduler.py's reconcile_stale_onpage_tasks), across
all projects, independent of anyone loading a page.
"""
from datetime import datetime

from . import models

STALE_TASK_HOURS = 6  # a 100-page DataForSEO crawl normally finishes in minutes


def mark_stale_onpage_tasks(db, project_id: int | None = None) -> list[int]:
    """Marks any 'posted' OnPageTask older than STALE_TASK_HOURS as 'error'.
    Returns the ids marked. Does not commit -- callers control the
    transaction boundary (the route commits alongside other work in the
    same request; the scheduler tick commits on its own)."""
    query = db.query(models.OnPageTask).filter(models.OnPageTask.status == "posted")
    if project_id is not None:
        query = query.filter(models.OnPageTask.project_id == project_id)

    now = datetime.utcnow()
    marked_ids = []
    for task in query.all():
        age_hours = (now - task.created_at).total_seconds() / 3600
        if age_hours > STALE_TASK_HOURS:
            task.status = "error"
            task.error = f"Marked stale after {STALE_TASK_HOURS}h with no result from DataForSEO."
            marked_ids.append(task.id)
    return marked_ids
