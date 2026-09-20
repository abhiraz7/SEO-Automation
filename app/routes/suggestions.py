import hashlib
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import ai_provider, models, prompt_builder
from ..ai_errors import AIGenerationError, ImageFetchError
from ..database import get_db
from ..services import context_builder

router = APIRouter()
logger = logging.getLogger("image_alt")

# Statuses that represent a real user decision. Regeneration must never
# delete these -- they're the learning dataset (V6). Only undecided/refused
# rows may be replaced by a fresh generation.
DECIDED_STATUSES = ("accepted", "edited", "deployed")


def _normalize_content(text: str) -> str:
    """trim + collapse whitespace + casefold -- the same "is this actually
    the same suggestion" comparison used for de-duplication everywhere in
    this module, so two suggestions that only differ by capitalization or
    stray spacing count as duplicates."""
    return " ".join((text or "").split()).casefold()


def content_hash(text: str) -> str:
    return hashlib.sha256(_normalize_content(text).encode("utf-8")).hexdigest()


def _generate_and_store(
    db: Session, project_id: int, page_id: int, issue_id: int,
    image_src: str | None = None, user_guidance: str | None = None,
) -> list[models.Suggestion]:
    page = db.get(models.Page, page_id)
    issue = db.get(models.Issue, issue_id)
    if not page or not issue:
        raise HTTPException(status_code=404)

    # Fetch/create the page's understanding (cached per crawl snapshot) before
    # generating, so the prompt gets the distilled JSON instead of raw fit_markdown.
    # None for pages with no crawl snapshot (DataForSEO/SEMrush-sourced) --
    # generation still works, just without that extra context. For image_alt
    # specifically, prompt_builder.build_suggestion_context also pulls
    # page_title/page_meta_description straight off models.Page regardless of
    # this -- see that function's docstring (Image Alt hardening Part 4).
    understanding_row = context_builder.build_page_understanding(db, page)

    profile = (
        db.query(models.BusinessProfile)
        .filter(models.BusinessProfile.project_id == page.project_id)
        .first()
    )
    context = prompt_builder.build_suggestion_context(
        page, issue, business_profile=profile,
        understanding=understanding_row.understanding_json if understanding_row else None,
        image={"src": image_src} if image_src else None,
        user_guidance=user_guidance,
    )

    # Suggestions are scoped by (issue_id, image_src) so a multi-image
    # image_alt issue keeps each image's suggestions independent -- an
    # image_alt issue with no image_src (single-image or pre-migration-027
    # rows) still behaves exactly as before, scoped to the whole issue.
    scope_filter = [models.Suggestion.issue_id == issue_id, models.Suggestion.image_src == image_src]

    # Only replace rows nobody has decided on -- accepted/edited/deployed
    # suggestions are recorded user decisions and must survive regeneration.
    db.query(models.Suggestion).filter(
        *scope_filter,
        models.Suggestion.status.notin_(DECIDED_STATUSES),
    ).delete(synchronize_session=False)
    db.commit()

    # A decided suggestion already represents whatever text it holds -- if
    # the model regenerates the same wording again, that's not a NEW option,
    # it's the same one the user already ruled on. Compare against the
    # user-facing value (edited_content if they edited it, else content).
    decided_hashes = {
        content_hash(s.edited_content or s.content)
        for s in db.query(models.Suggestion).filter(
            *scope_filter,
            models.Suggestion.status.in_(DECIDED_STATUSES),
        )
    }

    if issue.category == "image_alt":
        # Image Alt AI hardening pass: real vision input + structured JSON,
        # not the text-only numbered-list path every other category still
        # uses -- see ai_provider.generate_image_alt_suggestions. Errors are
        # NOT swallowed into an empty suggestion list -- Part 1 requires the
        # "couldn't actually see the image" case to surface, not silently
        # degrade to a filename-only guess, so both failure modes propagate
        # as real exceptions for the route to turn into an HTTP error.
        try:
            items = ai_provider.generate_image_alt_suggestions(db, context, image_src)
        except ImageFetchError:
            logger.warning("image_alt: could not fetch image for visual analysis: %r", image_src)
            raise
        except AIGenerationError:
            logger.warning("image_alt: provider returned unparseable suggestions for image %r", image_src)
            raise
        # reason/confidence are logged for now rather than persisted --
        # Suggestion has no columns for them and Part 7 says not to add
        # columns unless absolutely necessary; the UI doesn't need them to
        # render the existing accept/reject/edit flow, only the content text.
        for item in items:
            logger.info(
                "image_alt suggestion (image=%r, confidence=%.2f): %s -- %s",
                image_src, item.get("confidence", 0.0), item["alt_text"], item.get("reason", ""),
            )
        texts = [item["alt_text"] for item in items]
    else:
        texts = ai_provider.generate_suggestions(db, context)

    rows = []
    seen_hashes = set(decided_hashes)  # also guards against dupes *within* this same batch
    rank = 0
    for text in texts:
        h = content_hash(text)
        if h in seen_hashes:
            continue
        seen_hashes.add(h)
        rank += 1
        row = models.Suggestion(
            project_id=project_id,
            page_id=page_id,
            issue_id=issue_id,
            image_src=image_src,
            understanding_id=understanding_row.id if understanding_row else None,
            content=text,
            content_hash=h,
            rank=rank,
        )
        db.add(row)
        rows.append(row)

    try:
        db.commit()
    except IntegrityError:
        # Belt-and-braces: the checks above should already prevent this, but
        # a genuine race (two rapid Generate clicks committing between our
        # SELECT and our INSERT) could still hit the DB's unique constraint.
        # Recover by inserting whatever DID survive, one row at a time,
        # instead of losing the whole batch to one collision.
        db.rollback()
        survivors = []
        for row in rows:
            db.add(row)
            try:
                db.commit()
                survivors.append(row)
            except IntegrityError:
                db.rollback()
        rows = survivors

    for row in rows:
        db.refresh(row)
    return rows


def _suggestion_out(s: models.Suggestion) -> dict:
    return {
        "id": s.id,
        "status": s.status,
        "image_src": s.image_src,
        "content": s.content,
        "edited_content": s.edited_content,
        # Hardcoded -- Suggestion has no source column, so this doesn't
        # reflect which provider (Claude/Gemini) actually generated a given
        # row, only ever shows "claude". Fine while Claude is the only
        # provider in practice; needs a real column once Gemini suggestions
        # start getting generated, so old and new rows stay honestly labeled.
        "source": "claude",
        "rank": s.rank,
        "accepted_at": s.accepted_at,
        "deployed_at": s.deployed_at,
    }


@router.post("/projects/{project_id}/pages/{page_id}/issues/{issue_id}/suggest")
def generate(project_id: int, page_id: int, issue_id: int, db: Session = Depends(get_db)):
    _generate_and_store(db, project_id, page_id, issue_id)
    return RedirectResponse(url=f"/projects/{project_id}/pages/{page_id}", status_code=303)


class SuggestionEditIn(BaseModel):
    content: str


@router.post("/api/suggestions/manual")
def create_manual_suggestion(
    project_id: int,
    page_id: int,
    issue_id: int,
    payload: SuggestionEditIn,
    image_src: str | None = None,
    db: Session = Depends(get_db),
):
    """Lets the user type a fix straight into the editor without generating
    an AI suggestion first -- the fix-modal's textarea previously required
    picking 'Use in editor' on an existing row, so a user who already knew
    the fix had no way to save it. Stored as status 'edited' (no separate
    'manual' status exists) with edited_content == content, since that's
    the same shape edit_suggestion() produces and it dedupes/displays the
    same way. image_src scopes this to one image within a multi-image
    image_alt issue (migration 027) -- the same "type your own alt text"
    path the AI-generate option sits next to in the fix modal."""
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    h = content_hash(content)

    existing = db.query(models.Suggestion).filter(
        models.Suggestion.issue_id == issue_id,
        models.Suggestion.image_src == image_src,
        models.Suggestion.content_hash == h,
    ).first()
    if existing:
        if existing.status == "deployed":
            raise HTTPException(status_code=409, detail="Already deployed -- roll it back before changing its status.")
        existing.status = "edited"
        existing.edited_content = content
        existing.accepted_at = datetime.now(timezone.utc)
        db.commit()
        return _suggestion_out(existing)

    max_rank = db.query(models.Suggestion).filter(models.Suggestion.issue_id == issue_id).count()
    row = models.Suggestion(
        project_id=project_id,
        page_id=page_id,
        issue_id=issue_id,
        image_src=image_src,
        content=content,
        content_hash=h,
        rank=max_rank + 1,
        status="edited",
        edited_content=content,
        accepted_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _suggestion_out(row)


@router.post("/api/suggest")
def generate_json(
    project_id: int,
    page_id: int,
    issue_id: int,
    image_src: str | None = None,
    user_guidance: str | None = None,
    db: Session = Depends(get_db),
):
    """Generate AI suggestions (count set by prompt_builder.SUGGESTION_COUNT) and return as JSON for the inline optimize panel, including each row's id/status so the UI can act on a specific suggestion afterward. image_src scopes generation to one image within a multi-image image_alt issue (migration 027) -- omitted for every other category. user_guidance is an optional free-text preference (image_alt only, Image Alt hardening Part 5) -- ignored by every other category's prompt."""
    try:
        rows = _generate_and_store(db, project_id, page_id, issue_id, image_src=image_src, user_guidance=user_guidance)
    except ImageFetchError as e:
        # 422: the request was well-formed but the image genuinely couldn't
        # be visually analyzed (unreachable, wrong content-type, too large) --
        # a controlled, visible failure per Part 1, not a silent text-only
        # fallback pretending visual analysis happened.
        raise HTTPException(status_code=422, detail=f"Couldn't load this image for AI analysis: {e}")
    except AIGenerationError as e:
        # 502: our request was fine, the upstream provider's response wasn't
        # usable even after one retry.
        raise HTTPException(status_code=502, detail=f"AI provider returned an unusable response: {e}")
    return {"suggestions": [_suggestion_out(r) for r in rows]}


# ── Acceptance tracking (V6 / Task 3.1) ─────────────────────────────────


def _get_suggestion(db: Session, suggestion_id: int) -> models.Suggestion:
    suggestion = db.get(models.Suggestion, suggestion_id)
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return suggestion


@router.post("/suggestions/{suggestion_id}/accept")
def accept_suggestion(suggestion_id: int, db: Session = Depends(get_db)):
    suggestion = _get_suggestion(db, suggestion_id)
    if suggestion.status == "deployed":
        raise HTTPException(status_code=409, detail="Already deployed -- roll it back before changing its status.")
    suggestion.status = "accepted"
    suggestion.accepted_at = datetime.now(timezone.utc)
    db.commit()
    return _suggestion_out(suggestion)


@router.post("/suggestions/{suggestion_id}/reject")
def reject_suggestion(suggestion_id: int, db: Session = Depends(get_db)):
    suggestion = _get_suggestion(db, suggestion_id)
    if suggestion.status == "deployed":
        raise HTTPException(status_code=409, detail="Already deployed -- roll it back before changing its status.")
    suggestion.status = "rejected"
    suggestion.accepted_at = None
    db.commit()
    return _suggestion_out(suggestion)


@router.post("/suggestions/{suggestion_id}/edit")
def edit_suggestion(suggestion_id: int, payload: SuggestionEditIn, db: Session = Depends(get_db)):
    """Stores the user's modified version alongside the original -- the
    original content is never overwritten, since 'what the AI proposed vs.
    what the human changed it to' is exactly the signal the future learning
    dataset needs."""
    suggestion = _get_suggestion(db, suggestion_id)
    if suggestion.status == "deployed":
        raise HTTPException(status_code=409, detail="Already deployed -- roll it back before changing its status.")
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    suggestion.status = "edited"
    suggestion.edited_content = content
    suggestion.accepted_at = datetime.now(timezone.utc)
    db.commit()
    return _suggestion_out(suggestion)
