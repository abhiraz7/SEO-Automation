"""
Job handler for job_type='verify_deploy'. Runs a short while after a
successful WordPress deploy (see routes/wordpress.py deploy_suggestion,
which enqueues this job right after writing a SuggestionRevision) to answer
a question the deploy write itself can't: does the PUBLIC page actually
show the new value yet, or is a page/CDN cache still serving the old one.

Deliberately uses DataForSEO's instant_pages -- an external fetch from
DataForSEO's own infrastructure, not our server or the user's browser --
so a "verified" result means a real outside visitor would see the fix too,
not just that nothing local is caching it. Cost is not a concern here (see
project decision); correctness of the confirmation is what matters.

Same never-raise / always-finalize-the-job-row discipline as every other
handler in this package. A verification failure (DataForSEO error, page
unreachable, etc.) is recorded on the revision as verify_status="error"
and the JOB itself still completes -- an inability to verify is not a job
failure, it's a fact about the deploy that the UI should surface.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ... import dataforseo_onpage, deploy_status, models


# A page or CDN cache can keep serving the old value for minutes to an hour
# after a correct write. So a "mismatch" is not final on the first look: the
# check is repeated after these delays (minutes). Escalating rather than
# fixed, so a quick cache expiry is noticed fast and a slow one still gets
# caught, without hammering DataForSEO. After the last delay the mismatch
# stands -- it is then a real problem (theme override, wrong field, a cache
# that never clears), not a delay.
RECHECK_DELAYS_MINUTES = (3, 10, 30)


def _utcnow():
    return datetime.now(timezone.utc)


def _norm(text) -> str:
    """Whitespace/case-insensitive compare -- a live page re-render can
    introduce trivial differences (trailing space, smart-quote encoding)
    that are not a real deploy failure."""
    return " ".join(str(text or "").split()).casefold()


def _extract_live_value(field_name: str, item: dict):
    """Pulls the one field this revision cares about out of a raw DataForSEO
    instant_pages item. Deliberately reads the raw item rather than going
    through dataforseo_onpage.normalize_page() -- that helper only keeps
    twitter_card, not twitter:title, and this needs the exact field each
    FIELD_DEPLOYERS entry in routes/wordpress.py actually writes."""
    meta = item.get("meta") or {}
    social = dataforseo_onpage.social_tags(item)
    htags = meta.get("htags") or {}

    if field_name == "title":
        return meta.get("title")
    if field_name == "meta_description":
        return meta.get("description")
    if field_name == "h1":
        h1s = htags.get("h1") or []
        return h1s[0] if h1s else None
    if field_name == "canonical":
        return meta.get("canonical")
    if field_name == "opengraph":
        return social.get("og:title")
    if field_name == "twitter":
        return social.get("twitter:title") or social.get("twitter:card")
    return None


def _is_latest_active_revision(db: Session, revision: models.SuggestionRevision) -> bool:
    """A redeploy makes a newer revision for the same suggestion without rolling
    the old one back. Checking the old revision's value against the page would
    just report a false mismatch (the page now shows the newer value)."""
    newest = (
        db.query(models.SuggestionRevision.id)
        .filter(
            models.SuggestionRevision.suggestion_id == revision.suggestion_id,
            models.SuggestionRevision.rolled_back_at.is_(None),
        )
        .order_by(models.SuggestionRevision.id.desc())
        .first()
    )
    return bool(newest) and newest[0] == revision.id


def _open_verify_jobs(db: Session, revision_id: int, exclude_job_id: int | None = None) -> list:
    return [
        j for j in db.query(models.Job).filter(
            models.Job.job_type == "verify_deploy",
            models.Job.status.in_(("queued", "running", "waiting")),
        )
        if (j.payload or {}).get("revision_id") == revision_id and j.id != exclude_job_id
    ]


def _schedule_recheck(db: Session, job: models.Job, revision: models.SuggestionRevision, attempt: int) -> bool:
    """Parks the next check in status 'waiting' (see scheduler._promote_due_waiting_jobs
    for why not 'queued'). No-op once the delays are used up, or if another
    check for this revision is already pending."""
    if attempt > len(RECHECK_DELAYS_MINUTES):
        return False
    if _open_verify_jobs(db, revision.id, exclude_job_id=job.id):
        return False
    not_before = _utcnow() + timedelta(minutes=RECHECK_DELAYS_MINUTES[attempt - 1])
    db.add(models.Job(
        project_id=revision.project_id,
        job_type="verify_deploy",
        status="waiting",
        payload={"revision_id": revision.id, "attempt": attempt + 1, "not_before": not_before.isoformat()},
    ))
    return True


def run_verify_deploy_job(db: Session, job: models.Job) -> None:
    job.status = "running"
    job.started_at = _utcnow()
    job.attempts = (job.attempts or 0) + 1
    db.commit()

    revision_id = (job.payload or {}).get("revision_id")
    revision = db.get(models.SuggestionRevision, revision_id) if revision_id else None

    attempt = int((job.payload or {}).get("attempt") or 1)

    try:
        if not revision:
            raise ValueError(f"SuggestionRevision {revision_id!r} not found")

        if revision.rolled_back_at or not _is_latest_active_revision(db, revision):
            # Rolled back, or replaced by a newer deploy: nothing left to verify,
            # and checking would cost a DataForSEO call to report a false result.
            job.status = "completed"
            job.result_summary = {"revision_id": revision_id, "skipped": "rolled back or superseded"}
            return

        page = db.query(models.Page).join(
            models.Suggestion, models.Suggestion.page_id == models.Page.id
        ).filter(models.Suggestion.id == revision.suggestion_id).first()
        if not page or not page.url:
            revision.verify_status = "error"
            revision.verify_detail = "Could not resolve the page URL to verify."
        else:
            item = dataforseo_onpage.instant_page_check(page.url)
            if item.get("error"):
                revision.verify_status = "error"
                revision.verify_detail = item["error"]
            else:
                live_value = _extract_live_value(revision.field_name, item)
                if _norm(live_value) == _norm(revision.after_value):
                    revision.verify_status = "verified"
                    revision.verify_detail = live_value
                    # Only now -- with proof the public page shows it -- does
                    # our own Page copy ("Current" in the UI) take the new value.
                    deploy_status.apply_verified_value_to_page(
                        db, page.id, revision.field_name, revision.after_value
                    )
                else:
                    revision.verify_status = "mismatch"
                    revision.verify_detail = live_value or "(empty/not found on the live page)"

        revision.verify_checked_at = _utcnow()
        # Not verified yet (a cache may still be serving the old value) or the
        # check itself failed (possibly transient): look again later.
        rechecking = revision.verify_status in ("mismatch", "error") and _schedule_recheck(db, job, revision, attempt)
        db.commit()

        job.status = "completed"
        job.result_summary = {"revision_id": revision_id, "verify_status": revision.verify_status, "attempt": attempt, "recheck_scheduled": rechecking}
    except Exception as e:
        job.status = "failed"
        job.error = str(e)
    finally:
        job.finished_at = _utcnow()
        db.commit()
