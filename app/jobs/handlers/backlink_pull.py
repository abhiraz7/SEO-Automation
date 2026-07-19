"""
Job handler for job_type='backlink_pull' (Task 5.2). Fetches the project's
current backlink list from Semrush and diffs it against BacklinkRecord rows:
links present in both stay active (last_seen_at bumped, lost_at cleared if
it had been marked lost and came back); links in the fetch but not in the
DB are new (first_seen_at=last_seen_at=now); links in the DB as still-active
(lost_at IS NULL) but absent from this fetch are marked lost_at=now.

Also writes a BacklinkSnapshot (same aggregate overview as Task 5.1's manual
refresh) so the Backlinks panel's headline numbers stay current on a
schedule too, not just on manual refresh.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ... import backlinks_provider, models


def _utcnow():
    return datetime.now(timezone.utc)


def run_backlink_pull_job(db: Session, job: models.Job) -> None:
    job.status = "running"
    job.started_at = _utcnow()
    job.attempts = (job.attempts or 0) + 1
    db.commit()

    try:
        project = db.get(models.Project, job.project_id)
        if not project:
            raise ValueError(f"Project {job.project_id} not found")

        overview = backlinks_provider.get_backlinks_overview(project.base_url)
        if overview.status == "ok":
            db.add(models.BacklinkSnapshot(
                project_id=job.project_id,
                authority_score=overview.authority_score,
                referring_domains=overview.referring_domains,
                total_backlinks=overview.total_backlinks,
                follow_links=overview.follow_links,
                nofollow_links=overview.nofollow_links,
                source=overview.source,
                fetched_at=overview.fetched_at,
            ))

        listing = backlinks_provider.get_backlinks_list(project.base_url)
        if listing.get("error"):
            # Overview may have still succeeded above -- commit that, but the
            # diff itself can't proceed without the per-link list.
            db.commit()
            job.status = "failed"
            job.error = listing["error"]
            job.finished_at = _utcnow()
            db.commit()
            return

        fetched = {
            (row.get("source_url", ""), row.get("target_url", "")): row
            for row in listing["rows"]
            if row.get("source_url") and row.get("target_url")
        }

        existing = {
            (r.source_url, r.target_url): r
            for r in db.query(models.BacklinkRecord).filter(models.BacklinkRecord.project_id == job.project_id).all()
        }

        now = _utcnow()
        new_count, lost_count, active_count = 0, 0, 0

        for key, row in fetched.items():
            record = existing.get(key)
            if record is None:
                db.add(models.BacklinkRecord(
                    project_id=job.project_id,
                    source_url=key[0],
                    target_url=key[1],
                    anchor_text=row.get("anchor"),
                    is_follow=(row.get("nofollow", "").lower() != "true"),
                    first_seen_at=now,
                    last_seen_at=now,
                ))
                new_count += 1
            else:
                record.last_seen_at = now
                if record.lost_at is not None:
                    record.lost_at = None  # relinked
                active_count += 1

        for key, record in existing.items():
            if key not in fetched and record.lost_at is None:
                record.lost_at = now
                lost_count += 1

        db.commit()
        job.status = "completed"
        job.result_summary = {"new_links": new_count, "lost_links": lost_count, "still_active": active_count}
    except Exception as e:
        db.rollback()
        job.status = "failed"
        job.error = str(e)
    finally:
        job.finished_at = _utcnow()
        db.commit()
