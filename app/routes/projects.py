from datetime import datetime
from math import ceil

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import audit, models
from ..database import get_db

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

ISSUES_PAGE_SIZE = 10


def _time_ago(dt: datetime) -> str:
    seconds = (datetime.utcnow() - dt).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def _normalize_url(base_url: str) -> str:
    base_url = base_url.strip()
    if not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url
    return base_url.rstrip("/")


@router.get("/")
def index(request: Request, db: Session = Depends(get_db)):
    projects = db.query(models.Project).order_by(models.Project.created_at.desc()).all()
    return templates.TemplateResponse(request, "index.html", {"projects": projects})


@router.post("/projects")
def create_project(
    name: str = Form(...),
    base_url: str = Form(...),
    project_type: str = Form("manual"),
    db: Session = Depends(get_db),
):
    project = models.Project(
        name=name.strip(),
        base_url=_normalize_url(base_url),
        project_type=project_type,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return RedirectResponse(url=f"/projects/{project.id}", status_code=303)


@router.get("/projects/{project_id}")
def project_detail(project_id: int, request: Request, db: Session = Depends(get_db)):
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    pages = (
        db.query(models.Page)
        .filter(models.Page.project_id == project_id)
        .order_by(models.Page.url)
        .all()
    )
    return templates.TemplateResponse(
        request, "project_detail.html", {"project": project, "pages": pages}
    )


@router.get("/projects/{project_id}/pages/{page_id}")
def page_detail(
    project_id: int,
    page_id: int,
    request: Request,
    pg: int = 1,
    db: Session = Depends(get_db),
):
    page = db.get(models.Page, page_id)
    if not page or page.project_id != project_id:
        raise HTTPException(status_code=404, detail="Page not found")

    issues = page.issues
    other_issues = [issue for issue in issues if issue.category != "title"]
    total_pages = max(1, ceil(len(other_issues) / ISSUES_PAGE_SIZE))
    current_page = min(max(pg, 1), total_pages)
    start = (current_page - 1) * ISSUES_PAGE_SIZE

    return templates.TemplateResponse(
        request,
        "page_detail.html",
        {
            "page": page,
            "score": audit.page_score(issues) if not page.error else None,
            "title_checklist": audit.title_checklist(page, issues),
            "other_issues": other_issues[start : start + ISSUES_PAGE_SIZE],
            "other_issues_total": len(other_issues),
            "current_page": current_page,
            "total_pages": total_pages,
            "word_count": len((page.custom_content or "").split()),
            "crawled_ago": _time_ago(page.updated_at) if page.updated_at else None,
        },
    )
