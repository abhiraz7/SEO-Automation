"""
Competitor Analysis: Phase 2 (management) + Phase 3 (overview comparison).
Phase 4's data flow lives in app/services/competitor_analysis.py -- this
file is thin: validate input, call the service, render/return. No
provider-specific structures handled here at all.

The AI Competitor Gap Analysis (per-page SERP + content gaps + action plan) is
the same shape: app/services/competitor_gap.py does the work, the handlers below
only map its errors to HTTP statuses (bad input 400, already running 409, unknown
run/action 404, AI failure 502).
"""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import keyword_locations, models
from ..ai_errors import AIGenerationError
from ..database import get_db
from ..domain_utils import normalize_domain
from ..services import competitor_analysis, competitor_gap
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


def _get_run(db: Session, project_id: int, run_id: int) -> models.CompetitorAnalysisRun:
    """A run, but only through ITS OWN project's URL: /projects/1/... must never
    reveal or change a run that belongs to project 2."""
    run = db.get(models.CompetitorAnalysisRun, run_id)
    if not run or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return run


def _default_location(db: Session, project_id: int) -> str:
    workspace = db.query(models.KeywordWorkspace).filter(models.KeywordWorkspace.project_id == project_id).first()
    return (workspace.default_location if workspace and workspace.default_location else keyword_locations.DEFAULT_LOCATION)


@router.get("/projects/{project_id}/competitors")
def competitors_page(project_id: int, request: Request, run: int | None = None, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    competitors = _active_competitors(db, project_id)
    comparison = competitor_analysis.latest_comparison(db, project_id, competitors)
    gap_run = competitor_gap.run_detail(db, _get_run(db, project_id, run)) if run is not None else None
    page_urls = [
        row[0] for row in
        db.query(models.Page.url).filter(models.Page.project_id == project_id).order_by(models.Page.url).limit(300).all()
    ]
    return templates.TemplateResponse(
        request, "competitors.html", {
            "project": project,
            "competitors": competitors,
            "comparison": comparison,
            "gap_run": gap_run,
            "gap_runs": competitor_gap.recent_runs(db, project_id),
            "gap_locations": keyword_locations.supported_locations(),
            "gap_default_location": _default_location(db, project_id),
            "gap_page_urls": page_urls,
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


# ── AI Competitor Gap Analysis ────────────────────────────────────────────
# JSON endpoints called from competitors.html. Nothing here loads on a page view:
# every provider/AI call (which costs money) happens only from one of these POSTs.

@router.post("/projects/{project_id}/competitors/gap-analysis")
def start_gap_analysis(
    project_id: int,
    target_url: str = Form(""),
    keyword: str = Form(""),
    location: str = Form(""),
    device: str = Form("desktop"),
    with_plan: bool = Form(True),
    db: Session = Depends(get_db),
):
    """Runs one analysis to completion (it can take a minute or two: SERP call, up to
    7 page fetches, one AI call). A run that ends as error / no_data is still a 200:
    the run is saved and the page shows exactly what went wrong. with_plan=false
    gathers the evidence only (no AI call); the Content Optimizer asks for that."""
    project = _get_project(db, project_id)
    try:
        run = competitor_gap.run_analysis(db, project, target_url, keyword, location, device, with_plan=with_plan)
    except competitor_gap.InputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except competitor_gap.BusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"run_id": run.id, "status": run.status, "error": run.error, "url": f"/projects/{project_id}/competitors?run={run.id}"}


@router.get("/projects/{project_id}/competitors/gap-analysis/{run_id}")
def gap_analysis_detail(project_id: int, run_id: int, db: Session = Depends(get_db)):
    _get_project(db, project_id)
    return competitor_gap.run_detail(db, _get_run(db, project_id, run_id))


@router.post("/projects/{project_id}/competitors/gap-analysis/{run_id}/actions/{action_id}/draft")
def generate_action_draft(project_id: int, run_id: int, action_id: str, db: Session = Depends(get_db)):
    """One AI call for one action. Saves a 'pending' draft; nothing is published."""
    _get_project(db, project_id)
    run = _get_run(db, project_id, run_id)
    try:
        draft = competitor_gap.generate_draft(db, run, action_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except competitor_gap.InputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except competitor_gap.BusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except AIGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"draft": draft}


@router.post("/projects/{project_id}/competitors/gap-analysis/{run_id}/actions/{action_id}/decision")
def decide_action_draft(
    project_id: int,
    run_id: int,
    action_id: str,
    decision: str = Form(""),
    text: str = Form(""),
    db: Session = Depends(get_db),
):
    """Records accept / reject / edit for a draft. Only records: it does not publish."""
    _get_project(db, project_id)
    run = _get_run(db, project_id, run_id)
    try:
        draft = competitor_gap.decide_draft(db, run, action_id, decision, text)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except competitor_gap.InputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"draft": draft}
