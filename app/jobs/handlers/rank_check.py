"""
Job handler for job_type='rank_check' (Task 4.1). For every TrackedKeyword
in every KeywordWorkspace linked to the job's project, fetches the live SERP
(keyword_provider.get_serp, same DataForSEO-first/Semrush-fallback routing
as View SERP) and looks for the project's own domain among the organic
results, writing a NEW KeywordSnapshot with the found position (or None if
not found in the results actually returned).

Depth caveat: DataForSEO's serp/google/organic/live/advanced returns up to
~100 organic results per call; the Semrush phrase_organic fallback (used
when DataForSEO is down -- see dataforseo.py/semrush.py) only returns 10.
"Not found" therefore means "not in the top ~100" on DataForSEO but only
"not in the top 10" if Semrush answered instead -- the snapshot's source
field records which provider actually served it, so this isn't silently
conflated.
"""
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from ... import models
from ...keyword_provider import get_serp


def _utcnow():
    return datetime.now(timezone.utc)


def _domain(url: str) -> str:
    netloc = urlparse(url if "://" in url else f"//{url}").netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def _find_position(serp: dict, target_domain: str) -> int | None:
    for item in serp.get("items") or []:
        if item.get("type") != "organic":
            continue
        item_url = item.get("url") or ""
        if _domain(item_url) == target_domain:
            return item.get("rank_absolute") or item.get("rank_group")
    return None


def run_rank_check_job(db: Session, job: models.Job) -> None:
    job.status = "running"
    job.started_at = _utcnow()
    job.attempts = (job.attempts or 0) + 1
    db.commit()

    try:
        project = db.get(models.Project, job.project_id)
        if not project:
            raise ValueError(f"Project {job.project_id} not found")
        target_domain = _domain(project.base_url)

        workspaces = db.query(models.KeywordWorkspace).filter(
            models.KeywordWorkspace.project_id == job.project_id
        ).all()
        tracked = [
            kw for ws in workspaces
            for kw in db.query(models.TrackedKeyword).filter(models.TrackedKeyword.workspace_id == ws.id).all()
        ]

        checked, found, errored = 0, 0, 0
        for kw in tracked:
            try:
                serp = get_serp(kw.keyword, kw.location)
                checked += 1
                if serp.get("error"):
                    errored += 1
                    continue

                position = _find_position(serp, target_domain)
                if position is not None:
                    found += 1

                # Carry forward volume/difficulty/intent from the latest prior
                # snapshot so a rank-only check doesn't make _latest_snapshot_view
                # regress the Overview table's volume/difficulty display to
                # blank -- this snapshot's NEW information is the position.
                prior = max(kw.snapshots, key=lambda s: s.fetched_at) if kw.snapshots else None
                db.add(models.KeywordSnapshot(
                    tracked_keyword_id=kw.id,
                    volume=prior.volume if prior else None,
                    difficulty=prior.difficulty if prior else None,
                    intent=prior.intent if prior else None,
                    position=position,
                    source=serp.get("_source", "dataforseo"),
                    fetched_at=_utcnow(),
                ))
            except Exception:
                errored += 1
        db.commit()

        job.status = "completed"
        job.result_summary = {"keywords_checked": checked, "positions_found": found, "errors": errored}
    except Exception as e:
        job.status = "failed"
        job.error = str(e)
    finally:
        job.finished_at = _utcnow()
        db.commit()
