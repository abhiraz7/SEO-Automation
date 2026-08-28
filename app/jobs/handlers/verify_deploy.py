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
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ... import dataforseo_onpage, models


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
    social = item.get("social_media_tags") or {}
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


def run_verify_deploy_job(db: Session, job: models.Job) -> None:
    job.status = "running"
    job.started_at = _utcnow()
    job.attempts = (job.attempts or 0) + 1
    db.commit()

    revision_id = (job.payload or {}).get("revision_id")
    revision = db.get(models.SuggestionRevision, revision_id) if revision_id else None

    try:
        if not revision:
            raise ValueError(f"SuggestionRevision {revision_id!r} not found")

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
                else:
                    revision.verify_status = "mismatch"
                    revision.verify_detail = live_value or "(empty/not found on the live page)"

        revision.verify_checked_at = _utcnow()
        db.commit()

        job.status = "completed"
        job.result_summary = {"revision_id": revision_id, "verify_status": revision.verify_status}
    except Exception as e:
        job.status = "failed"
        job.error = str(e)
    finally:
        job.finished_at = _utcnow()
        db.commit()
