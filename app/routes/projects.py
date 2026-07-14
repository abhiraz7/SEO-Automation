import os
from collections import defaultdict
from datetime import datetime
from math import ceil

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import audit, models, schemas
from ..semrush import fetch_domain_metrics
from ..database import get_db

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["current_value_for"] = audit.current_value_for
templates.env.globals["RULE_REQUIREMENTS"] = audit.RULE_REQUIREMENTS

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


PROFILE_FIELDS = ["brand", "industry", "services", "locations", "audiences", "tone", "usp"]


@router.get("/projects/{project_id}/business-profile", response_model=schemas.BusinessProfileOut)
def get_business_profile(project_id: int, db: Session = Depends(get_db)):
    profile = (
        db.query(models.BusinessProfile)
        .filter(models.BusinessProfile.project_id == project_id)
        .first()
    )
    if not profile:
        raise HTTPException(status_code=404, detail="No business profile for this project")
    return profile


@router.post("/projects/{project_id}/business-profile", response_model=schemas.BusinessProfileOut)
def save_business_profile(
    project_id: int,
    payload: schemas.BusinessProfileIn,
    db: Session = Depends(get_db),
):
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    profile = (
        db.query(models.BusinessProfile)
        .filter(models.BusinessProfile.project_id == project_id)
        .first()
    )
    if not profile:
        profile = models.BusinessProfile(project_id=project_id)
        db.add(profile)

    for field in PROFILE_FIELDS:
        setattr(profile, field, getattr(payload, field))

    db.commit()
    db.refresh(profile)
    return profile


@router.post("/projects/{project_id}/delete")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    db.delete(project)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


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
    semrush_connected = bool(os.environ.get("SEMRUSH_API_KEY", "").strip())
    semrush_data = fetch_domain_metrics(project.base_url) if semrush_connected else {}

    page_data = []
    for page in pages:
        issues = (
            db.query(models.Issue)
            .filter(models.Issue.page_id == page.id)
            .all()
        )
        suggestions = (
            db.query(models.Suggestion)
            .filter(models.Suggestion.page_id == page.id)
            .order_by(models.Suggestion.rank)
            .all()
        )
        sugg_by_issue = {}
        for s in suggestions:
            sugg_by_issue.setdefault(s.issue_id, []).append(s)
        title_issues = [i for i in issues if i.category == "title"]
        checklist = audit.title_checklist(page, issues)
        page_data.append({
            "page": page,
            "issues": issues,
            "suggestions": suggestions,
            "sugg_by_issue": sugg_by_issue,
            "title_issues": title_issues,
            "checklist": checklist,
            "title_len": len(page.title or ""),
            "meta_len": len(page.meta_description or ""),
        })

    # Group all issues across pages by category for accordion view
    grouped_issues = defaultdict(list)
    for item in page_data:
        for issue in item["issues"]:
            grouped_issues[issue.category].append({
                "page": item["page"],
                "message": issue.message,
                "severity": issue.severity,
                "issue_id": issue.id,
            })
    # Sort categories: most errors first
    grouped_issues = dict(
        sorted(
            grouped_issues.items(),
            key=lambda x: sum(1 for i in x[1] if i["severity"] == "error"),
            reverse=True,
        )
    )

    last_page = max(pages, key=lambda p: p.updated_at or datetime.min) if pages else None
    last_crawled_ago = _time_ago(last_page.updated_at) if last_page and last_page.updated_at else "Never"

    profile = (
        db.query(models.BusinessProfile)
        .filter(models.BusinessProfile.project_id == project_id)
        .first()
    )

    return templates.TemplateResponse(
        request, "project_detail.html", {
            "project": project,
            "pages": pages,
            "page_data": page_data,
            "grouped_issues": grouped_issues,
            "semrush_connected": semrush_connected,
            "semrush_data": semrush_data,
            "last_crawled_ago": last_crawled_ago,
            "profile": profile,
        }
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
