"""
/projects/{project_id}/onpage -- the DataForSEO/SEMrush-sourced on-page
audit view, with the exact Fix/Suggest/Deploy flow prototyped in
SEO-AUTOMATION-LITE (app/projects/{id} there). Generalized to any project
(originally hard-pinned to project 1 while this was being built/verified).

Which provider a project uses is decided once, at creation (project.type
in Project.project_type: "dataforseo" | "semrush"), not by a global
toggle -- each project can use a different provider. /settings' provider
toggle now only reflects API-key configuration status, not per-project
routing.

Crawler data is never shown here: every query below filters
Page.source == project's provider, so the retiring crawler+audit.py
pipeline (Page.source == "crawler", the default) is completely invisible
on this page while staying untouched everywhere else in the app.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import audit, dataforseo_onpage, models, wordpress
from ..database import get_db
from .links import store_links_for_task
from .settings import get_site_audit_cooldown_hours, register_crawler_global

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["current_value_display"] = audit.current_value_display
register_crawler_global(templates)

CATEGORY_COLORS = {
    "title": "#4f46e5", "meta_description": "#0284c7", "h1": "#7c3aed",
    "image_alt": "#0891b2", "canonical": "#b45309", "opengraph": "#c026d3",
    "twitter": "#1d4ed8", "content": "#d97706", "security": "#dc2626",
}
CATEGORY_LABELS = {
    "title": "Meta Title", "meta_description": "Meta Description", "h1": "H1 Heading",
    "image_alt": "Image Alt Text", "canonical": "Canonical Link", "opengraph": "Open Graph",
    "twitter": "Twitter Card", "content": "Content Quality", "security": "Security",
}
DEPLOYABLE_CATEGORIES = ["meta_description", "title", "h1", "twitter", "canonical", "opengraph"]
PROVIDER_LABELS = {"dataforseo": "DataForSEO", "semrush": "SEMrush"}
ONPAGE_PROVIDERS = ("dataforseo", "semrush")


def _get_project(db: Session, project_id: int) -> models.Project:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def project_provider(project: models.Project, db: Session) -> str:
    """Which provider THIS project uses. Data-driven first -- if it already
    has DataForSEO or SEMrush pages, that's authoritative -- falling back
    to project.project_type (the choice made at creation) only when no
    provider pages exist yet. Never trusts project_type alone: a project
    whose stored type disagrees with its actual data (e.g. typed "semrush"
    but only ever crawler-fetched) must not silently show an empty page --
    routes/projects.py's redirect already keeps such projects on the
    legacy crawler view instead of sending them here at all, but this
    stays data-driven too as a second line of defense."""
    source_counts = dict(
        db.query(models.Page.source, func.count(models.Page.id))
        .filter(models.Page.project_id == project.id, models.Page.source.in_(ONPAGE_PROVIDERS))
        .group_by(models.Page.source)
        .all()
    )
    for preferred in ("dataforseo", "semrush"):
        if source_counts.get(preferred):
            return preferred
    return project.project_type if project.project_type in ONPAGE_PROVIDERS else "dataforseo"


def _target_domain(project: models.Project) -> str:
    return project.base_url.replace("https://", "").replace("http://", "").rstrip("/")


# ── Ingestion (DataForSEO -> Page/Issue, source="dataforseo") ───────────

def _store_page_result(db: Session, project: models.Project, item: dict, onpage_task_id: int | None) -> models.Page:
    normalized = dataforseo_onpage.normalize_page(item)
    url = normalized["url"]

    page = (
        db.query(models.Page)
        .filter(models.Page.project_id == project.id, models.Page.url == url, models.Page.source == "dataforseo")
        .first()
    )
    if not page:
        page = models.Page(project_id=project.id, url=url, source="dataforseo")
        db.add(page)

    for field, value in normalized.items():
        if field == "url":
            continue
        setattr(page, field, value)
    page.onpage_task_id = onpage_task_id
    page.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(page)

    # Replace this page's issues wholesale on every fresh fetch -- a stale
    # issue that DataForSEO no longer flags must not linger in the list.
    db.query(models.Issue).filter(models.Issue.page_id == page.id).delete(synchronize_session=False)
    for issue_dict in dataforseo_onpage.issues_from_item(item):
        db.add(models.Issue(project_id=project.id, page_id=page.id, **issue_dict))
    db.commit()
    return page


@router.post("/projects/{project_id}/onpage/instant-check")
def instant_check(project_id: int, url: str = Form(...), db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    result = dataforseo_onpage.instant_page_check(url.strip())
    if result.get("error"):
        raise HTTPException(status_code=502, detail=result["error"])
    _store_page_result(db, project, result, onpage_task_id=None)
    return RedirectResponse(url=f"/projects/{project_id}/onpage", status_code=303)


@router.post("/projects/{project_id}/onpage/site-audit/start")
def start_site_audit(project_id: int, max_crawl_pages: int = Form(20), db: Session = Depends(get_db)):
    """Guarded on two fronts before ever calling DataForSEO (each billed):
    an in-flight (status='posted') task always blocks a new one outright --
    no legitimate reason to run two crawls of the same site at once -- and,
    on top of that, a completed run within the last cooldown_hours (see
    /settings, models.SiteAuditSettings) blocks a fresh one too, since page
    content/SEO metadata essentially never changes meaningfully faster than
    that. Both exist because of a real incident: a double-clicked "Refresh
    now" button with no submit-guard fired 13 billed crawls of the same 20
    pages in 4 seconds -- see AgentLog for the debugging writeup."""
    project = _get_project(db, project_id)

    in_flight = (
        db.query(models.OnPageTask)
        .filter(models.OnPageTask.project_id == project_id, models.OnPageTask.status == "posted")
        .first()
    )
    if in_flight:
        return RedirectResponse(
            url=f"/projects/{project_id}/onpage?refresh_blocked=A+crawl+is+already+in+progress+for+this+project+%28started+{in_flight.created_at.strftime('%Y-%m-%d %H:%M')}%29.+Wait+for+it+to+finish+before+starting+another.",
            status_code=303,
        )

    cooldown_hours = get_site_audit_cooldown_hours(db)
    if cooldown_hours > 0:
        last_fetched = (
            db.query(models.OnPageTask)
            .filter(models.OnPageTask.project_id == project_id, models.OnPageTask.status == "fetched")
            .order_by(models.OnPageTask.finished_at.desc())
            .first()
        )
        if last_fetched and last_fetched.finished_at:
            # Naive UTC math, matching routes/projects.py._time_ago's convention --
            # finished_at is written via datetime.now(timezone.utc) but SQLite
            # stores/reads it back naive, so comparing against an aware "now"
            # would raise TypeError.
            elapsed = datetime.utcnow() - last_fetched.finished_at
            remaining = cooldown_hours * 3600 - elapsed.total_seconds()
            if remaining > 0:
                remaining_h = round(remaining / 3600, 1)
                return RedirectResponse(
                    url=f"/projects/{project_id}/onpage?refresh_blocked=Last+refresh+was+{last_fetched.finished_at.strftime('%Y-%m-%d %H:%M')}.+Wait+about+{remaining_h}h+more+%28cooldown%3A+{cooldown_hours}h%2C+see+Settings%29+or+use+Instant+Check+for+a+single+URL.",
                    status_code=303,
                )

    result = dataforseo_onpage.start_site_task(_target_domain(project), max_crawl_pages=max_crawl_pages)
    if result.get("error"):
        raise HTTPException(status_code=502, detail=result["error"])

    task = models.OnPageTask(
        project_id=project.id,
        dataforseo_task_id=result["task_id"],
        max_crawl_pages=max_crawl_pages,
        status="posted",
    )
    db.add(task)
    db.commit()
    return RedirectResponse(url=f"/projects/{project_id}/onpage", status_code=303)


@router.post("/projects/{project_id}/onpage/site-audit/{task_id}/check")
def check_site_audit(project_id: int, task_id: int, db: Session = Depends(get_db)):
    """Poll-and-pull in one action, triggered by an explicit 'Check status'
    click -- no background poller, matches 'everything on runtime'."""
    task = db.get(models.OnPageTask, task_id)
    if not task or task.project_id != project_id:
        raise HTTPException(status_code=404, detail="Task not found")
    project = _get_project(db, project_id)

    if task.status == "fetched":
        return RedirectResponse(url=f"/projects/{project_id}/onpage", status_code=303)

    if not dataforseo_onpage.is_task_ready(task.dataforseo_task_id):
        return RedirectResponse(url=f"/projects/{project_id}/onpage", status_code=303)

    result = dataforseo_onpage.fetch_task_pages(task.dataforseo_task_id)
    if result.get("error"):
        task.status = "error"
        task.error = result["error"]
        db.commit()
        return RedirectResponse(url=f"/projects/{project_id}/onpage", status_code=303)

    for item in result.get("pages") or []:
        _store_page_result(db, project, item, onpage_task_id=task.id)

    task.status = "fetched"
    task.pages_crawled = len(result.get("pages") or [])
    task.finished_at = datetime.now(timezone.utc)
    db.commit()

    # Link Analyzer's data source -- same task_id, no separate DataForSEO
    # cost. Best-effort: a links failure must not roll back the page/issue
    # ingestion above, which already succeeded and committed.
    store_links_for_task(db, project_id, task)

    return RedirectResponse(url=f"/projects/{project_id}/onpage", status_code=303)


# ── View ──────────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/onpage")
def onpage_view(project_id: int, request: Request, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    active_provider = project_provider(project, db)

    pages = (
        db.query(models.Page)
        .filter(models.Page.project_id == project.id, models.Page.source == active_provider)
        .order_by(models.Page.updated_at.desc())
        .all()
    )
    page_ids = [p.id for p in pages]
    issues = (
        db.query(models.Issue)
        .filter(models.Issue.page_id.in_(page_ids))
        .order_by(models.Issue.severity.desc(), models.Issue.created_at.desc())
        .all()
        if page_ids else []
    )
    tasks = (
        db.query(models.OnPageTask)
        .filter(models.OnPageTask.project_id == project.id)
        .order_by(models.OnPageTask.created_at.desc())
        .all()
    )
    wp_conn = db.query(models.WordPressConnection).filter(models.WordPressConnection.project_id == project.id).first()
    profile = db.query(models.BusinessProfile).filter(models.BusinessProfile.project_id == project.id).first()

    wp_token_preview = None
    if wp_conn:
        try:
            token = wordpress.decrypt_token(wp_conn.api_token)
            wp_token_preview = f"{token[:4]}{'•' * 8}{token[-4:]}" if len(token) > 8 else "•" * len(token)
        except RuntimeError:
            wp_token_preview = "(unreadable — re-enter token)"

    pages_by_id = {p.id: p for p in pages}
    grouped_issues: dict[str, list] = {}
    for issue in issues:
        grouped_issues.setdefault(issue.category, []).append(issue)

    total_issues = len(issues)
    error_count = sum(1 for i in issues if i.severity == "error")
    warning_count = sum(1 for i in issues if i.severity == "warning")
    suggestion_count = (
        db.query(models.Suggestion).filter(models.Suggestion.page_id.in_(page_ids)).count() if page_ids else 0
    )
    total_pages = len(pages)

    issues_js = {
        issue.id: {
            "id": issue.id,
            "page_id": issue.page_id,
            "project_id": project.id,
            "category": issue.category,
            "rule": issue.rule,
            "severity": issue.severity,
            "message": issue.message,
            "url": pages_by_id.get(issue.page_id).url if pages_by_id.get(issue.page_id) else "",
            "suggestions": [
                {
                    "id": s.id,
                    "status": s.status,
                    "content": s.content,
                    "edited_content": s.edited_content,
                    "source": "claude",
                    "rank": s.rank,
                }
                for s in sorted(
                    db.query(models.Suggestion).filter(models.Suggestion.issue_id == issue.id).all(),
                    key=lambda s: s.rank,
                )
            ],
        }
        for issue in issues
    }

    return templates.TemplateResponse(
        request, "onpage_semrush.html", {
            "project": project,
            "target": _target_domain(project),
            "pages": pages,
            "pages_by_id": pages_by_id,
            "grouped_issues": grouped_issues,
            "cat_colors": CATEGORY_COLORS,
            "cat_labels": CATEGORY_LABELS,
            "tasks": tasks,
            "wp_conn": wp_conn,
            "wp_token_preview": wp_token_preview,
            "profile": profile,
            "wp_error": request.query_params.get("wp_error"),
            "refresh_blocked": request.query_params.get("refresh_blocked"),
            "total_pages": total_pages,
            "total_issues": total_issues,
            "error_count": error_count,
            "warning_count": warning_count,
            "suggestion_count": suggestion_count,
            "issues_js": issues_js,
            "deployable_categories": DEPLOYABLE_CATEGORIES,
            "active_provider": active_provider,
            "active_provider_label": PROVIDER_LABELS.get(active_provider, active_provider),
            "not_configured": not dataforseo_onpage.is_configured(),
        }
    )
