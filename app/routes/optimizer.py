"""
AI Content Optimizer: the page and its JSON endpoints. Thin, like routes/competitors.py:
validate, call app/services/content_optimizer.py, map its errors to HTTP statuses
(bad input 400, unknown page / run 404, already running 409). A run that ends as
error / no_data / no_change is still a 200: the run is saved and the page shows exactly
what happened.

Accept / edit / reject / deploy / roll back are NOT here: the review page calls the
existing /suggestions/... and /revisions/... routes, so there is one approval workflow.
Nothing runs on a page view: every search or AI call (which costs money) happens only
from one of the POSTs below.
"""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import keyword_locations, models
from ..database import get_db
from ..services import competitor_gap, content_optimizer
from .settings import register_crawler_global

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
register_crawler_global(templates)


def _get_project(db: Session, project_id: int) -> models.Project:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _get_run(db: Session, project_id: int, run_id: int) -> models.ContentOptimizationRun:
    """A run, but only through ITS OWN project's URL."""
    run = db.get(models.ContentOptimizationRun, run_id)
    if not run or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Optimization not found")
    return run


def _default_location(db: Session, project_id: int) -> str:
    workspace = db.query(models.KeywordWorkspace).filter(models.KeywordWorkspace.project_id == project_id).first()
    return workspace.default_location if workspace and workspace.default_location else keyword_locations.DEFAULT_LOCATION


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except content_optimizer.InputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except content_optimizer.BusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/projects/{project_id}/optimizer")
def optimizer_page(project_id: int, request: Request, run: int | None = None, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    detail = content_optimizer.run_detail(db, _get_run(db, project_id, run)) if run is not None else None
    return templates.TemplateResponse(
        request, "optimizer.html", {
            "project": project,
            "pages": content_optimizer.selectable_pages(db, project_id),
            "runs": content_optimizer.recent_runs(db, project_id),
            "run": detail,
            "locations": keyword_locations.supported_locations(),
            "default_location": _default_location(db, project_id),
        }
    )


@router.post("/projects/{project_id}/optimizer/evidence")
def gather_evidence(
    project_id: int,
    page_id: int = Form(0),
    keyword: str = Form(""),
    location: str = Form(""),
    device: str = Form("desktop"),
    refresh: bool = Form(False),
    db: Session = Depends(get_db),
):
    """Step 1: reuse a fresh stored analysis of this page and keyword, or gather new search
    evidence (one search-results call plus the competitor pages; no AI call). A failed or
    empty analysis is still a 200: step 2 records it as the run's outcome."""
    project = _get_project(db, project_id)
    run, reused = _call(content_optimizer.gather_evidence, db, project, page_id, keyword, location, device, refresh)
    return {"evidence_run_id": run.id, "reused": reused, "status": run.status, "error": run.error, "summary_line": competitor_gap.summary_line(run)}


@router.post("/projects/{project_id}/optimizer/run")
def start_optimization(
    project_id: int,
    page_id: int = Form(0),
    keyword: str = Form(""),
    location: str = Form(""),
    device: str = Form("desktop"),
    evidence_run_id: int | None = Form(None),
    refresh_evidence: bool = Form(False),
    db: Session = Depends(get_db),
):
    """Step 2: one AI call over that evidence; every suggestion is validated and stored."""
    project = _get_project(db, project_id)
    run = _call(content_optimizer.run_optimization, db, project, page_id, keyword, location, device, evidence_run_id, refresh_evidence)
    return {"run_id": run.id, "status": run.status, "error": run.error, "url": f"/projects/{project_id}/optimizer?run={run.id}"}


@router.get("/projects/{project_id}/optimizer/runs/{run_id}")
def optimization_detail(project_id: int, run_id: int, db: Session = Depends(get_db)):
    _get_project(db, project_id)
    return content_optimizer.run_detail(db, _get_run(db, project_id, run_id))     # diff_html is Markup (a str): already escaped, serialises as text
