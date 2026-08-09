"""
Competitor Analysis: Phase 2 (management) + Phase 3 (overview comparison).
Phase 4's data flow lives in app/services/competitor_analysis.py -- this
file is thin: validate input, call the service, render/return. No
provider-specific structures handled here at all.
"""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..domain_utils import normalize_domain
from ..services import competitor_analysis
from .settings import register_crawler_global

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
register_crawler_global(templates)


def _get_project(db: Session, project_id: int) -> models.Project:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _active_competitors(db: Session, project_id: int) -> list[models.Competitor]:
    return (
        db.query(models.Competitor)
        .filter(models.Competitor.project_id == project_id, models.Competitor.is_active.is_(True))
        .order_by(models.Competitor.created_at)
        .all()
    )


@router.get("/projects/{project_id}/competitors")
def competitors_page(project_id: int, request: Request, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    competitors = _active_competitors(db, project_id)
    comparison = competitor_analysis.latest_comparison(db, project_id, competitors)
    return templates.TemplateResponse(
        request, "competitors.html", {
            "project": project,
            "competitors": competitors,
            "comparison": comparison,
        }
    )


@router.post("/projects/{project_id}/competitors")
def add_competitor(
    project_id: int,
    domain: str = Form(...),
    display_name: str = Form(""),
    db: Session = Depends(get_db),
):
    project = _get_project(db, project_id)
    normalized = normalize_domain(domain)
    if not normalized:
        raise HTTPException(status_code=400, detail="A valid domain is required.")

    row = models.Competitor(
        project_id=project.id,
        domain=normalized,
        display_name=display_name.strip() or normalized,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"{normalized} is already a competitor for this project.")
    return RedirectResponse(url=f"/projects/{project_id}/competitors", status_code=303)


@router.post("/projects/{project_id}/competitors/{competitor_id}/edit")
def edit_competitor(
    project_id: int,
    competitor_id: int,
    display_name: str = Form(...),
    db: Session = Depends(get_db),
):
    row = db.get(models.Competitor, competitor_id)
    if not row or row.project_id != project_id:
        raise HTTPException(status_code=404, detail="Competitor not found")
    row.display_name = display_name.strip() or row.domain
    db.commit()
    return RedirectResponse(url=f"/projects/{project_id}/competitors", status_code=303)


@router.post("/projects/{project_id}/competitors/{competitor_id}/deactivate")
def deactivate_competitor(project_id: int, competitor_id: int, db: Session = Depends(get_db)):
    row = db.get(models.Competitor, competitor_id)
    if not row or row.project_id != project_id:
        raise HTTPException(status_code=404, detail="Competitor not found")
    row.is_active = False
    db.commit()
    return RedirectResponse(url=f"/projects/{project_id}/competitors", status_code=303)


@router.post("/projects/{project_id}/competitors/refresh")
def refresh_comparison(project_id: int, db: Session = Depends(get_db)):
    """The only place that actually calls provider APIs for this module --
    never triggered by a page load, only this explicit action."""
    project = _get_project(db, project_id)
    competitors = _active_competitors(db, project_id)
    competitor_analysis.refresh_project_comparison(db, project, competitors)
    return RedirectResponse(url=f"/projects/{project_id}/competitors", status_code=303)
