import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models, prompt_builder
from .. import claude as claude_client
from ..database import get_db
from ..services import context_builder

router = APIRouter()

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


def _generate_and_store(db: Session, project_id: int, page_id: int, issue_id: int) -> list[models.Suggestion]:
    page = db.get(models.Page, page_id)
    issue = db.get(models.Issue, issue_id)
    if not page or not issue:
        raise HTTPException(status_code=404)

    # Fetch/create the page's understanding (cached per crawl snapshot) before
    # generating, so the prompt gets the distilled JSON instead of raw fit_markdown.
    understanding_row = context_builder.build_page_understanding(db, page)

    profile = (
        db.query(models.BusinessProfile)
        .filter(models.BusinessProfile.project_id == page.project_id)
        .first()
    )
    context = prompt_builder.build_suggestion_context(
        page, issue, business_profile=profile, understanding=understanding_row.understanding_json
    )

    # Only replace rows nobody has decided on -- accepted/edited/deployed
    # suggestions are recorded user decisions and must survive regeneration.
    db.query(models.Suggestion).filter(
        models.Suggestion.issue_id == issue_id,
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
            models.Suggestion.issue_id == issue_id,
            models.Suggestion.status.in_(DECIDED_STATUSES),
        )
    }

    texts = claude_client.generate_suggestions(context)
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
            understanding_id=understanding_row.id,
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
        "content": s.content,
        "edited_content": s.edited_content,
        "rank": s.rank,
        "accepted_at": s.accepted_at,
        "deployed_at": s.deployed_at,
    }


@router.post("/projects/{project_id}/pages/{page_id}/issues/{issue_id}/suggest")
def generate(project_id: int, page_id: int, issue_id: int, db: Session = Depends(get_db)):
    _generate_and_store(db, project_id, page_id, issue_id)
    return RedirectResponse(url=f"/projects/{project_id}/pages/{page_id}", status_code=303)


@router.post("/api/suggest")
def generate_json(
    project_id: int,
    page_id: int,
    issue_id: int,
    db: Session = Depends(get_db),
):
    """Generate AI suggestions (count set by prompt_builder.SUGGESTION_COUNT) and return as JSON for the inline optimize panel, including each row's id/status so the UI can act on a specific suggestion afterward."""
    rows = _generate_and_store(db, project_id, page_id, issue_id)
    return {"suggestions": [_suggestion_out(r) for r in rows]}


# ── Acceptance tracking (V6 / Task 3.1) ─────────────────────────────────


class SuggestionEditIn(BaseModel):
    content: str


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
