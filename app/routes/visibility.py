"""
AI Visibility -- checks whether a project's own domain shows up in Google's
real AI Overview citations and/or organic rankings, for queries built from
the project's business profile. Runs live against DataForSEO's SERP API
(dataforseo.fetch_serp, /serp/google/organic/live/advanced) -- every field
recorded is a value the API actually returned (ai_overview.references,
organic item .domain/.rank_absolute), nothing invented or scored by a
made-up formula.
"""
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import dataforseo, models
from ..database import get_db
from .security import _visible_projects
from .settings import register_crawler_global

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
register_crawler_global(templates)


def _project_domain(project: models.Project) -> str:
    return urlparse(project.base_url if "://" in project.base_url else f"//{project.base_url}").netloc.lower().removeprefix("www.")


def suggested_queries(profile: models.BusinessProfile | None) -> list[str]:
    """Built straight from the business profile's own fields -- services x
    locations, services x audiences -- no invented keyword logic beyond
    combining what the user already entered in the profile."""
    if not profile:
        return []

    def _flatten(items: list[str]) -> list[str]:
        # Profile entity fields are meant to be one value per list item, but
        # some were saved as a single comma-packed string (e.g.
        # ["SEO, PPC, website traffic"]) -- split on commas too so either
        # shape produces clean individual terms instead of one run-on query.
        out = []
        for item in items or []:
            out.extend(p.strip() for p in item.split(",") if p.strip())
        return out

    services = _flatten(profile.services)[:2]
    locations = [l for l in _flatten(profile.locations) if l.lower() not in ("local", "unites states", "united states")][:2]
    audiences = _flatten(profile.audiences)[:1]

    queries = []
    for service in services:
        for location in locations:
            queries.append(f"best {service} in {location}")
        for audience in audiences:
            queries.append(f"{service} for {audience}")
        if not locations and not audiences:
            queries.append(f"best {service}")
    return queries[:5]


def run_visibility_check(db: Session, project: models.Project, query: str) -> models.VisibilityCheck:
    domain = _project_domain(project)
    result = dataforseo.fetch_serp(query, location="US")

    check = models.VisibilityCheck(project_id=project.id, query=query)

    if result.get("error"):
        check.raw_error = result["error"]
        db.add(check)
        db.commit()
        db.refresh(check)
        return check

    items = result.get("items") or []
    ai_overview = next((i for i in items if i.get("type") == "ai_overview"), None)
    organic = [i for i in items if i.get("type") == "organic"]

    competitor_domains = []

    if ai_overview:
        check.ai_overview_present = True
        references = ai_overview.get("references") or []
        check.ai_overview_references = [
            {"domain": r.get("domain"), "source": r.get("source"), "url": r.get("url"), "title": r.get("title")}
            for r in references
        ]
        ref_domains = {(r.get("domain") or "").lower().removeprefix("www.") for r in references}
        check.brand_in_ai_overview = domain in ref_domains
        competitor_domains.extend(d for d in ref_domains if d and d != domain)

    own_organic = next((o for o in organic if (o.get("domain") or "").lower().removeprefix("www.") == domain), None)
    if own_organic:
        check.organic_rank = own_organic.get("rank_absolute")

    for o in organic[:10]:
        d = (o.get("domain") or "").lower().removeprefix("www.")
        if d and d != domain and d not in competitor_domains:
            competitor_domains.append(d)
    check.competitor_domains = competitor_domains[:10]

    db.add(check)
    db.commit()
    db.refresh(check)
    return check


@router.get("/visibility")
def visibility_list(request: Request, db: Session = Depends(get_db)):
    projects = _visible_projects(db)
    return templates.TemplateResponse(request, "visibility_list.html", {"projects": projects})


@router.get("/projects/{project_id}/visibility")
def visibility_report(project_id: int, request: Request, db: Session = Depends(get_db)):
    project = db.get(models.Project, project_id)
    if not project:
        return templates.TemplateResponse(request, "visibility_list.html", {"projects": _visible_projects(db)})

    profile = db.query(models.BusinessProfile).filter(models.BusinessProfile.project_id == project_id).first()
    checks = (
        db.query(models.VisibilityCheck)
        .filter(models.VisibilityCheck.project_id == project_id)
        .order_by(models.VisibilityCheck.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        request, "visibility_report.html", {
            "project": project,
            "domain": _project_domain(project),
            "checks": checks,
            "suggested": suggested_queries(profile),
            "has_profile": profile is not None,
            "dataforseo_configured": dataforseo.is_configured(),
        }
    )


@router.post("/projects/{project_id}/visibility/check")
def visibility_check(project_id: int, query: str = Form(...), db: Session = Depends(get_db)):
    project = db.get(models.Project, project_id)
    if project and query.strip():
        run_visibility_check(db, project, query.strip())
    return RedirectResponse(url=f"/projects/{project_id}/visibility", status_code=303)
