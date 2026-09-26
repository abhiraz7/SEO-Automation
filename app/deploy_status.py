"""
One place that answers "is this deployed suggestion actually live?".

suggestion.status == "deployed" only means WordPress accepted our write. Whether
the PUBLIC page shows it is a separate fact, recorded on the latest
SuggestionRevision by the verify_deploy job (app/jobs/handlers/verify_deploy.py).
Every serializer that sends suggestions to the UI goes through here so the
dashboard, the Fix-on-Page modal and the project view can never disagree about
what "deployed" means.

live_status values (what the UI badges on):
  checking     -- deployed, verification not finished yet
  live         -- the public page shows the deployed value
  not_showing  -- written to WordPress, but the public page still shows
                  something else (page cache, a theme override, or the wrong
                  SEO plugin's fields). NOT a success.
  unverified   -- we couldn't run the check (page unreachable, API error)
"""
from sqlalchemy.orm import Session

from . import models

_LIVE_STATUS_BY_VERIFY = {
    "pending": "checking",
    "verified": "live",
    "mismatch": "not_showing",
    "error": "unverified",
}


def live_status_from_verify(verify_status: str | None) -> str:
    """Unknown/NULL verify_status is treated as 'checking' -- never as 'live'.
    Failing closed is deliberate: a badge must not claim success it can't back."""
    return _LIVE_STATUS_BY_VERIFY.get(verify_status or "pending", "checking")


def live_status_for_suggestions(db: Session, suggestion_ids: list[int]) -> dict[int, dict]:
    """{suggestion_id: {live_status, live_detail, revision_id}} for every
    suggestion that currently has a non-rolled-back revision. Suggestions that
    were never deployed (or were rolled back) are simply absent, so callers
    use .get(). One query for the whole batch."""
    if not suggestion_ids:
        return {}
    revisions = (
        db.query(models.SuggestionRevision)
        .filter(
            models.SuggestionRevision.suggestion_id.in_(suggestion_ids),
            models.SuggestionRevision.rolled_back_at.is_(None),
        )
        .order_by(models.SuggestionRevision.id.desc())
        .all()
    )
    out: dict[int, dict] = {}
    for rev in revisions:
        # Newest first, so the first one seen per suggestion is the one that counts.
        if rev.suggestion_id in out:
            continue
        out[rev.suggestion_id] = {
            "live_status": live_status_from_verify(rev.verify_status),
            "live_detail": rev.verify_detail,
            "revision_id": rev.id,
        }
    return out


def suggestion_live_fields(status_map: dict[int, dict], suggestion: models.Suggestion) -> dict:
    """The extra keys to merge into a suggestion's JSON. Empty for anything not
    deployed, so non-deployed cards are unchanged. A 'deployed' suggestion with
    no revision row (shouldn't happen) reports 'unverified', not 'live'."""
    if suggestion.status != "deployed":
        return {}
    info = status_map.get(suggestion.id)
    if not info:
        return {"live_status": "unverified", "live_detail": None, "live_revision_id": None}
    return {
        "live_status": info["live_status"],
        "live_detail": info["live_detail"],
        "live_revision_id": info["revision_id"],
    }


# Category -> Page column that "Current" (current_value_for in audit.py) reads
# for that category. h1 is stored as a list on Page (the crawler can see
# multiple H1s); a deploy only ever supplies one value, so it replaces the list
# with a single-item list -- an approximation for pages that genuinely had >1
# H1, but a stale display would be a worse default than an accurate single value.
_PAGE_FIELD_FOR_CATEGORY = {
    "title": "title", "meta_description": "meta_description", "h1": "h1",
    "twitter": "twitter_title", "canonical": "canonical", "opengraph": "og_title",
}


def apply_verified_value_to_page(db: Session, page_id: int, field_name: str, new_value: str) -> None:
    """Copies a deployed value into our own crawled Page row so 'Current' stops
    showing the pre-deploy value. Called ONLY once the live page has confirmed
    the value (verify_status == 'verified'), never at write time -- otherwise
    our records would claim a change the public page doesn't show. Does not
    commit; the caller owns the transaction."""
    page = db.get(models.Page, page_id)
    if not page:
        return
    page_field = _PAGE_FIELD_FOR_CATEGORY.get(field_name)
    if not page_field:
        return
    if page_field == "h1":
        page.h1 = [new_value]
    else:
        setattr(page, page_field, new_value)
