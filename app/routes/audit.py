from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import audit as audit_engine
from .. import crawler, models
from ..database import get_db
from . import crawl

router = APIRouter()


def _get_project(db: Session, project_id: int) -> models.Project:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _persist_issues(db: Session, project_id: int, issues_by_page: dict):
    db.query(models.Issue).filter(models.Issue.project_id == project_id).delete()
    for page_id, issues in issues_by_page.items():
        for issue in issues:
            db.add(models.Issue(project_id=project_id, page_id=page_id, **issue))
    db.commit()


@router.post("/projects/{project_id}/audit")
def run_audit(project_id: int, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    pages = db.query(models.Page).filter(models.Page.project_id == project_id).all()

    issues = audit_engine.merge_issue_dicts(
        audit_engine.run_audit(pages),
        audit_engine.run_security_audit(project.base_url, pages),
    )
    _persist_issues(db, project_id, issues)

    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/pages/{page_id}/reaudit")
def reaudit_page(project_id: int, page_id: int, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    page = db.get(models.Page, page_id)
    if not page or page.project_id != project_id:
        raise HTTPException(status_code=404, detail="Page not found")

    data = crawler.crawl_single_page(page.url)
    crawl.upsert_page(db, project_id, data)

    pages = db.query(models.Page).filter(models.Page.project_id == project_id).all()
    issues = audit_engine.merge_issue_dicts(
        audit_engine.run_audit(pages),
        audit_engine.run_security_audit(project.base_url, pages),
    )
    _persist_issues(db, project_id, issues)

    return RedirectResponse(url=f"/projects/{project_id}/pages/{page_id}", status_code=303)


@router.post("/api/validate-rule")
def validate_rule(category: str = Form(...), value: str = Form(...)):
    """Check whether a candidate replacement value (e.g. an AI suggestion the user is
    about to save) would actually pass the audit rule for its category."""
    return audit_engine.validate_value(category, value)
