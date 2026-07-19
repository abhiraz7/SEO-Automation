from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
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


def _generate_and_store(db: Session, project_id: int, page_id: int, issue_id: int) -> list[str]:
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

    texts = claude_client.generate_suggestions(context)
    for rank, text in enumerate(texts, start=1):
        db.add(models.Suggestion(
            project_id=project_id,
            page_id=page_id,
            issue_id=issue_id,
            understanding_id=understanding_row.id,
            content=text,
            rank=rank,
        ))
    db.commit()
    return texts


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
    """Generate AI suggestions (count set by prompt_builder.SUGGESTION_COUNT) and return as JSON for the inline optimize panel."""
    texts = _generate_and_store(db, project_id, page_id, issue_id)
    return {"suggestions": texts}


# ── Acceptance tracking (V6 / Task 3.1) ─────────────────────────────────


class SuggestionEditIn(BaseModel):
    content: str


def _get_suggestion(db: Session, suggestion_id: int) -> models.Suggestion:
    suggestion = db.get(models.Suggestion, suggestion_id)
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return suggestion


def _suggestion_out(s: models.Suggestion) -> dict:
    return {
        "id": s.id,
        "status": s.status,
        "content": s.content,
        "edited_content": s.edited_content,
        "accepted_at": s.accepted_at,
        "deployed_at": s.deployed_at,
    }


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
