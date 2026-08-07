"""
Security Testing -- a standalone FE tool built on the SSL/headers/robots.txt
checks that already existed in audit.py (Task 6.3) but were only ever
reachable buried inside the retiring crawler flow, tied to a crawled
homepage Page row. This decouples them: runs live against any project's
base_url on demand, no crawl required, works regardless of which on-page
provider (or none) that project uses.
"""
import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from .projects import _project_active_source
from .settings import is_crawler_enabled, register_crawler_global

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
register_crawler_global(templates)

SECURITY_HEADERS = {
    "strict-transport-security": "Strict-Transport-Security",
    "x-content-type-options": "X-Content-Type-Options",
    "x-frame-options": "X-Frame-Options",
    "content-security-policy": "Content-Security-Policy",
}
_TIMEOUT = 10.0


def run_security_check(base_url: str) -> dict:
    """Live check against base_url -- SSL, each security header individually
    (not just a combined pass/fail), and robots.txt. Returns a report dict
    the template renders directly; never raises, every failure mode is
    captured as a result field instead."""
    report = {
        "base_url": base_url,
        "https": base_url.lower().startswith("https://"),
        "reachable": False,
        "connect_error": None,
        "headers": [],
        "robots": {"present": False, "status_code": None, "error": None},
    }

    try:
        resp = httpx.get(base_url, timeout=_TIMEOUT, follow_redirects=True)
        report["reachable"] = True
        report["status_code"] = resp.status_code
        for key, label in SECURITY_HEADERS.items():
            report["headers"].append({"label": label, "present": key in resp.headers})
    except httpx.RequestError as e:
        report["connect_error"] = str(e)

    robots_url = base_url.rstrip("/") + "/robots.txt"
    try:
        r = httpx.get(robots_url, timeout=_TIMEOUT, follow_redirects=True)
        report["robots"]["status_code"] = r.status_code
        report["robots"]["present"] = r.status_code == 200
    except httpx.RequestError as e:
        report["robots"]["error"] = str(e)

    checks_passed = sum([
        report["https"],
        report["reachable"] and not report["connect_error"],
        all(h["present"] for h in report["headers"]) if report["headers"] else False,
        report["robots"]["present"],
    ])
    report["score"] = int(checks_passed / 4 * 100)
    return report


def _visible_projects(db: Session) -> list[models.Project]:
    """Same crawler-hidden rule as the homepage project list (index()) --
    a crawler-sourced project must stay invisible everywhere, including
    here, while the crawler is disabled."""
    all_projects = db.query(models.Project).order_by(models.Project.created_at.desc()).all()
    if is_crawler_enabled(db):
        return all_projects
    return [p for p in all_projects if _project_active_source(p, db) != "crawler"]


@router.get("/security")
def security_list(request: Request, db: Session = Depends(get_db)):
    projects = _visible_projects(db)
    return templates.TemplateResponse(request, "security_list.html", {"projects": projects})


@router.get("/projects/{project_id}/security")
def security_report(project_id: int, request: Request, db: Session = Depends(get_db)):
    project = db.get(models.Project, project_id)
    if not project:
        return templates.TemplateResponse(request, "security_list.html", {"projects": _visible_projects(db)})
    report = run_security_check(project.base_url)
    return templates.TemplateResponse(
        request, "security_report.html", {"project": project, "report": report}
    )
