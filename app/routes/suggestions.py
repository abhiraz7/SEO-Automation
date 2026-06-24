from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import models
from .. import claude as claude_client
from ..database import get_db

router = APIRouter()

def _page_context(page: models.Page, issue: models.Issue) -> dict:
    field_map = {
        "title": page.title,
        "meta_description": page.meta_description,
        "h1": str(page.h1),
        "canonical": page.canonical,
        "og_title": page.og_title,
    }
    return {
        "url": page.url,
        "current_value": field_map.get(issue.category, "N/A"),
    }

@router.post("/projects/{project_id}/pages/{page_id}/issues/{issue_id}/suggest")
def generate(project_id: int, page_id: int, issue_id: int, db: Session = Depends(get_db)):
    page = db.get(models.Page, page_id)
    issue = db.get(models.Issue, issue_id)
    if not page or not issue:
        raise HTTPException(status_code=404)

    # Clear old suggestions for this issue
    db.query(models.Suggestion).filter(models.Suggestion.issue_id == issue_id).delete()
    db.commit()

    texts = claude_client.generate_suggestions(
        issue.category, issue.message, _page_context(page, issue)
    )
    for rank, text in enumerate(texts, start=1):
        db.add(models.Suggestion(
            project_id=project_id,
            page_id=page_id,
            issue_id=issue_id,
            content=text,
            rank=rank,
        ))
    db.commit()
    return RedirectResponse(url=f"/projects/{project_id}/pages/{page_id}", status_code=303)
