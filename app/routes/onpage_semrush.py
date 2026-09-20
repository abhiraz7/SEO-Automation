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
import csv
import io
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import audit, dataforseo_onpage, models, wordpress
from ..database import get_db
from ..onpage_task_maintenance import mark_stale_onpage_tasks
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
    "title": "📝 Meta Title", "meta_description": "📄 Meta Description", "h1": "🔠 H1 Heading",
    "image_alt": "🖼️ Image Alt Text", "canonical": "🔗 Canonical Link", "opengraph": "📱 Open Graph",
    "twitter": "🐦 Twitter Card", "content": "✍️ Content Quality", "security": "🔒 Security",
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

    # DataForSEO's on-page response has no per-image alt-text list (only the
    # checks.no_image_alt boolean) -- see dataforseo_onpage.fetch_image_alts.
    # Only fetch when the check actually flagged something, so a clean page
    # never costs an extra HTTP request against the target site.
    if normalized.get("checks", {}).get("no_image_alt"):
        page.image_alts = dataforseo_onpage.fetch_image_alts(url)
    else:
        page.image_alts = []

    page.onpage_task_id = onpage_task_id
    page.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(page)

    # Reconcile by (category, rule) instead of wiping and recreating every
    # Issue row on each fetch. An Issue's id is what every Suggestion is
    # anchored to (issue_id FK), including accepted/edited/deployed ones --
    # the V6 learning dataset (see suggestions.py's DECIDED_STATUSES). The
    # old delete-all-then-insert-all wiped every Issue row on every refresh,
    # so a fresh crawl that still (or again) flagged the same problem got a
    # brand-new Issue.id with no suggestions attached -- the fix modal showed
    # "No suggestions yet" for a fix that had already been deployed, which
    # looked exactly like the deploy had silently reverted even when
    # WordPress still had the deployed value live. This keeps a still-present
    # issue's row (and its suggestion/deploy history) stable across refreshes.
    existing_by_key = {
        (i.category, i.rule): i
        for i in db.query(models.Issue).filter(models.Issue.page_id == page.id).all()
    }
    seen_keys = set()
    for issue_dict in dataforseo_onpage.issues_from_item(item):
        key = (issue_dict["category"], issue_dict["rule"])
        seen_keys.add(key)
        existing = existing_by_key.get(key)
        if existing:
            existing.severity = issue_dict["severity"]
            existing.message = issue_dict["message"]
        else:
            db.add(models.Issue(project_id=project.id, page_id=page.id, **issue_dict))

    # Only issues DataForSEO no longer flags at all get removed -- via ORM
    # delete (not a bulk query.delete()) so Issue.suggestions' cascade="all,
    # delete-orphan" actually fires, since the issue is genuinely resolved
    # here, not just re-detected under a new row.
    for key, issue in existing_by_key.items():
        if key not in seen_keys:
            db.delete(issue)

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


MAX_CRAWL_PAGES_CAP = 1000  # ceiling against a fat-fingered value billing a huge crawl by accident


@router.post("/projects/{project_id}/onpage/site-audit/start")
def start_site_audit(project_id: int, max_crawl_pages: int = Form(100), db: Session = Depends(get_db)):
    """Guarded on two fronts before ever calling DataForSEO (each billed):
    an in-flight (status='posted') task always blocks a new one outright --
    no legitimate reason to run two crawls of the same site at once -- and,
    on top of that, a completed run within the last cooldown_hours (see
    /settings, models.SiteAuditSettings) blocks a fresh one too, since page
    content/SEO metadata essentially never changes meaningfully faster than
    that. Both exist because of a real incident: a double-clicked "Refresh
    now" button with no submit-guard fired 13 billed crawls of the same 20
    pages in 4 seconds -- see AgentLog for the debugging writeup.

    max_crawl_pages is now user-chosen (see the refresh modal's page-count
    field) instead of always 100, clamped here the same way
    save_site_audit_cooldown clamps its input -- never trust a form value
    as-is when it drives a billed external call."""
    max_crawl_pages = max(1, min(max_crawl_pages, MAX_CRAWL_PAGES_CAP))
    project = _get_project(db, project_id)

    in_flight = (
        db.query(models.OnPageTask)
        .filter(models.OnPageTask.project_id == project_id, models.OnPageTask.status == "posted")
        .first()
    )
    if in_flight:
        # A task DataForSEO never finishes (crawl died on their end, account
        # issue, etc.) never shows up in tasks_ready -- is_task_ready just
        # keeps returning False forever, so without this it blocks every
        # future crawl on this project permanently. Staleness check (and the
        # scheduler's proactive version of the same check) lives in
        # onpage_task_maintenance.py, not inline here -- see that module's
        # docstring for why this used to be reactive-only and stay stuck for
        # days.
        if mark_stale_onpage_tasks(db, project_id=project_id):
            db.commit()
        else:
            # Give a concrete wait estimate instead of an open-ended "wait for
            # it to finish" -- DataForSEO's task API has no progress percentage,
            # so this is the same rough elapsed/usually-takes heuristic the
            # audit-status-bar already shows while a crawl is in flight
            # (onpage_semrush.html's auditStatusText()), just computed here too
            # since a user who clicks Refresh again never sees that bar's text.
            elapsed_min = max(0, round((datetime.utcnow() - in_flight.created_at).total_seconds() / 60))
            est_min = max(1, round((in_flight.max_crawl_pages or 100) / 90))
            message = (
                f"A crawl is already in progress for this project (started "
                f"{in_flight.created_at.strftime('%Y-%m-%d %H:%M')}, {elapsed_min}m ago). "
                f"Usually takes about {est_min}m for up to {in_flight.max_crawl_pages or 100} pages -- "
                f"check back shortly, or wait here and it'll finish on its own."
            )
            return RedirectResponse(
                url=f"/projects/{project_id}/onpage?refresh_blocked={quote(message)}",
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


@router.post("/projects/{project_id}/onpage/site-audit/max-pages")
def save_default_max_pages(project_id: int, max_crawl_pages: int = Form(...), db: Session = Depends(get_db)):
    """Persists the Refresh modal's 'Max pages' value as this project's
    default (migration 026), so it's remembered next time instead of
    resetting to 100. Same clamp as start_site_audit -- a saved default is
    still a value that ends up driving a billed call later, so it gets the
    same never-trust-the-form-value treatment. JSON in/out (called via
    fetch from the Save button, not a real form submit) since this doesn't
    navigate anywhere."""
    project = _get_project(db, project_id)
    project.default_max_crawl_pages = max(1, min(max_crawl_pages, MAX_CRAWL_PAGES_CAP))
    db.commit()
    return {"default_max_crawl_pages": project.default_max_crawl_pages}


@router.post("/projects/{project_id}/onpage/site-audit/{task_id}/check")
def check_site_audit(project_id: int, task_id: int, db: Session = Depends(get_db)):
    """Poll-and-pull in one action. Previously triggered by a manual 'Check
    status' button click; now called automatically by the status-strip poll
    in onpage_semrush.html (see its block status_bar) every few seconds
    while a task is in flight, so returns JSON instead of redirecting --
    nothing renders a <form> against this route anymore."""
    task = db.get(models.OnPageTask, task_id)
    if not task or task.project_id != project_id:
        raise HTTPException(status_code=404, detail="Task not found")
    project = _get_project(db, project_id)

    if task.status == "fetched":
        return {"status": "fetched", "pages_crawled": task.pages_crawled}

    # A task can already be 'error' here without this request having done
    # anything -- onpage_task_maintenance.mark_stale_onpage_tasks (run by the
    # scheduler, see app/scheduler.py) marks tasks 'error' in the background
    # after STALE_TASK_HOURS. Before this check existed, that background
    # update was invisible to the poller: it fell through to is_task_ready()
    # again, which keeps returning False for a dead task, so the frontend
    # (onpage_semrush.html's pollSiteAuditTasks) never saw anything but
    # 'posted' and spun forever even after the backend knew the task had
    # failed.
    if task.status == "error":
        return {"status": "error", "error": task.error}

    if not dataforseo_onpage.is_task_ready(task.dataforseo_task_id):
        return {"status": "posted"}

    result = dataforseo_onpage.fetch_task_pages(task.dataforseo_task_id, limit=task.max_crawl_pages)
    if result.get("error"):
        task.status = "error"
        task.error = result["error"]
        db.commit()
        return {"status": "error", "error": result["error"]}

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

    return {"status": "fetched", "pages_crawled": task.pages_crawled}


# ── Export ────────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/onpage/export")
def export_onpage_report(project_id: int, db: Session = Depends(get_db)):
    """One row per page, mirroring what onpage_view shows on screen --
    same active_provider filter, so a client only ever exports the
    provider actually powering their view (never crawler-sourced rows)."""
    project = _get_project(db, project_id)
    active_provider = project_provider(project, db)

    pages = (
        db.query(models.Page)
        .filter(models.Page.project_id == project.id, models.Page.source == active_provider)
        .order_by(models.Page.updated_at.desc())
        .all()
    )
    page_ids = [p.id for p in pages]
    issues_by_page: dict[int, list[models.Issue]] = {pid: [] for pid in page_ids}
    if page_ids:
        for issue in db.query(models.Issue).filter(models.Issue.page_id.in_(page_ids)).all():
            issues_by_page[issue.page_id].append(issue)

    buf = io.StringIO()
    buf.write("﻿")  # BOM so Excel opens non-ASCII titles/URLs as UTF-8, not mojibake
    writer = csv.writer(buf)
    writer.writerow([
        "Page URL", "Status Code", "On-Page Score", "Word Count",
        "Title", "Meta Description", "H1", "Canonical",
        "Errors", "Warnings", "Issues", "Last Checked",
    ])
    for page in pages:
        page_issues = issues_by_page.get(page.id, [])
        writer.writerow([
            page.url,
            page.status_code,
            page.onpage_score,
            page.word_count,
            page.title or "",
            page.meta_description or "",
            "; ".join(page.h1) if page.h1 else "",
            page.canonical or "",
            sum(1 for i in page_issues if i.severity == "error"),
            sum(1 for i in page_issues if i.severity == "warning"),
            "; ".join(f"[{i.category}] {i.message}" for i in page_issues),
            page.updated_at.strftime("%Y-%m-%d %H:%M") if page.updated_at else "",
        ])
    buf.seek(0)

    filename = f"onpage-report-{_target_domain(project)}.csv"
    return StreamingResponse(
        buf,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


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
    # Always show every known category, even at 0 issues, so a client with a
    # genuinely clean site (or one where DataForSEO simply never flagged a
    # given category) doesn't look like its data is missing -- only shows up
    # after there's at least one crawled page, since before that "0 issues"
    # would be a lack of data, not a clean bill of health.
    grouped_issues: dict[str, list] = {cat: [] for cat in CATEGORY_LABELS} if pages else {}
    for issue in issues:
        grouped_issues.setdefault(issue.category, []).append(issue)

    total_issues = len(issues)
    error_count = sum(1 for i in issues if i.severity == "error")
    warning_count = sum(1 for i in issues if i.severity == "warning")
    suggestion_count = (
        db.query(models.Suggestion).filter(models.Suggestion.page_id.in_(page_ids)).count() if page_ids else 0
    )
    total_pages = len(pages)

    # SPIKE: cross-page grouping for missing-alt images (test before keeping --
    # revert this block + the also_on bit below if it doesn't hold up).
    # A single image (logo, banner, template asset) is often reused across
    # many pages, so DataForSEO/the crawler flags it as "missing alt" once
    # per page it appears on -- with no signal that it's actually one fix,
    # not N. This indexes every already-fetched page's images by resolved
    # src so the fix modal can say "also missing alt on these other pages",
    # telling the SEO team a single CMS/media-library edit will cover all of
    # them. Built once from data already in memory (pages_by_id, populated
    # by dataforseo_onpage.fetch_image_alts during ingestion) -- no new DB
    # query, no new HTTP call.
    image_src_to_pages: dict[str, set[str]] = {}
    for page in pages:
        for img in (page.image_alts or []):
            src = img.get("src")
            if src and not (img.get("alt") or "").strip():
                image_src_to_pages.setdefault(src, set()).add(page.url)

    def _missing_alt_images(issue: models.Issue) -> list[dict] | None:
        # Only image_alt issues carry per-image detail (see
        # dataforseo_onpage.fetch_image_alts) -- every other category
        # returns None so the fix modal knows not to render an image list.
        if issue.category != "image_alt":
            return None
        page = pages_by_id.get(issue.page_id)
        images = (page.image_alts if page else None) or []
        current_url = page.url if page else None
        result = []
        for img in images:
            if (img.get("alt") or "").strip():
                continue
            src = img.get("src")
            also_on = sorted(image_src_to_pages.get(src, set()) - {current_url}) if src else []
            result.append({
                "src": src,
                "alt": img.get("alt"),
                "also_on": also_on,
                # Editor-inserted images carry this straight from the HTML
                # (see html_extract._wp_media_id) -- theme-level images
                # (logo, header/footer) have none, since WordPress doesn't
                # stamp a class on those. None here means "no one-click
                # fix path yet" until the URL-lookup plugin tool exists.
                "media_id": img.get("media_id"),
            })
        return result

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
            "missing_alt_images": _missing_alt_images(issue),
            "suggestions": [
                {
                    "id": s.id,
                    "status": s.status,
                    "image_src": s.image_src,
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
            "max_crawl_pages_cap": MAX_CRAWL_PAGES_CAP,
            "default_max_crawl_pages": project.default_max_crawl_pages or 100,
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
