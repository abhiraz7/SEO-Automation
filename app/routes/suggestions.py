from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import models, prompt_builder
from .. import claude as claude_client
from ..database import get_db
from ..services import context_builder

router = APIRouter()


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

    db.query(models.Suggestion).filter(models.Suggestion.issue_id == issue_id).delete()
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
