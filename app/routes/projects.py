import os
from collections import defaultdict
from datetime import datetime
from math import ceil

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import audit, backlinks_provider, models, schemas
from ..semrush import fetch_domain_metrics
from ..database import get_db

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["current_value_for"] = audit.current_value_for
templates.env.globals["RULE_REQUIREMENTS"] = audit.RULE_REQUIREMENTS

ISSUES_PAGE_SIZE = 10


CRAWL_SETTINGS_PAYLOAD_FIELDS = [
    "user_agent", "max_depth", "crawl_delay_ms", "timeout_s", "respect_robots", "exclude_patterns",
    "worker_count", "concurrency", "retry_attempts", "worker_timeout_s",
    "firecrawl_validation", "coverage_target",
]


def _crawl_settings_out(schedule: models.Schedule | None) -> schemas.CrawlSettingsOut:
    """Schedule row (or None, before a project has ever saved settings) ->
    the flat shape the drawer's form fields expect. Defaults here match the
    drawer's original hardcoded HTML values, so an unconfigured project looks
    identical to today until someone actually saves."""
    payload = (schedule.payload if schedule else None) or {}
    defaults = schemas.CrawlSettingsIn().model_dump()
    fields = {k: payload.get(k, defaults[k]) for k in CRAWL_SETTINGS_PAYLOAD_FIELDS}
    return schemas.CrawlSettingsOut(
        id=schedule.id if schedule else 0,
        enabled=schedule.enabled if schedule else defaults["enabled"],
        interval=schedule.interval if schedule else defaults["interval"],
        timezone=schedule.timezone if schedule else defaults["timezone"],
        cron_expression=schedule.cron_expression if schedule else defaults["cron_expression"],
        last_run_at=schedule.last_run_at if schedule else None,
        next_run_at=schedule.next_run_at if schedule else None,
        **fields,
    )


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

    crawl_schedule = (
        db.query(models.Schedule)
        .filter(models.Schedule.project_id == project_id, models.Schedule.job_type == "crawl")
        .first()
    )
    wordpress_connection = (
        db.query(models.WordPressConnection)
        .filter(models.WordPressConnection.project_id == project_id)
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
            "crawl_settings": _crawl_settings_out(crawl_schedule),
            "wordpress_connection": wordpress_connection,
        }
    )


@router.post("/projects/{project_id}/crawl-settings", response_model=schemas.CrawlSettingsOut)
def save_crawl_settings(project_id: int, payload: schemas.CrawlSettingsIn, db: Session = Depends(get_db)):
    """Upserts the project's crawl Schedule row. Automation fields land on
    Schedule's own columns; everything else goes into payload (see
    CrawlSettingsIn's docstring). Does not compute next_run_at -- that's the
    scheduler's job (Task 2.4), not the save endpoint's."""
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    schedule = (
        db.query(models.Schedule)
        .filter(models.Schedule.project_id == project_id, models.Schedule.job_type == "crawl")
        .first()
    )
    if not schedule:
        schedule = models.Schedule(project_id=project_id, job_type="crawl")
        db.add(schedule)

    schedule.enabled = payload.enabled
    schedule.interval = payload.interval
    schedule.timezone = payload.timezone
    schedule.cron_expression = payload.cron_expression
    schedule.payload = {field: getattr(payload, field) for field in CRAWL_SETTINGS_PAYLOAD_FIELDS}
    if schedule.next_run_at is None:
        # Without this, a schedule saved mid-session sits with next_run_at=NULL
        # until the app restarts (only _backfill_next_run_at computes it, and
        # that only runs at scheduler startup) -- "Save" would silently do
        # nothing until then. Compute it here so it's live on the next tick.
        from ..scheduler import compute_next_run_at
        schedule.next_run_at = compute_next_run_at(schedule)

    db.commit()
    db.refresh(schedule)
    return _crawl_settings_out(schedule)


@router.get("/projects/{project_id}/backlinks")
def backlinks_overview(project_id: int, db: Session = Depends(get_db)):
    """Backlinks tab (Task 5.1): latest snapshot if one exists, else null --
    never a fabricated 0. Does NOT auto-fetch on every page load (that would
    burn a paid Semrush call per view); use the refresh endpoint below."""
    if not db.get(models.Project, project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    latest = (
        db.query(models.BacklinkSnapshot)
        .filter(models.BacklinkSnapshot.project_id == project_id)
        .order_by(models.BacklinkSnapshot.fetched_at.desc())
        .first()
    )
    if not latest:
        return {"status": "no_snapshot_yet"}
    return {
        "status": "ok",
        "authority_score": latest.authority_score,
        "referring_domains": latest.referring_domains,
        "total_backlinks": latest.total_backlinks,
        "follow_links": latest.follow_links,
        "nofollow_links": latest.nofollow_links,
        "source": latest.source,
        "fetched_at": latest.fetched_at,
    }


@router.post("/projects/{project_id}/backlinks/refresh")
def refresh_backlinks(project_id: int, db: Session = Depends(get_db)):
    """Manual refresh -- live Semrush pull, stores a new BacklinkSnapshot on
    success. A failed/no-data pull writes nothing, same discipline as every
    other provider integration in this codebase."""
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    overview = backlinks_provider.get_backlinks_overview(project.base_url)
    if overview.status == "error":
        raise HTTPException(status_code=502, detail=overview.error)
    if overview.status == "no_data":
        raise HTTPException(status_code=404, detail="Semrush has no backlink data for this domain.")

    db.add(models.BacklinkSnapshot(
        project_id=project_id,
        authority_score=overview.authority_score,
        referring_domains=overview.referring_domains,
        total_backlinks=overview.total_backlinks,
        follow_links=overview.follow_links,
        nofollow_links=overview.nofollow_links,
        source=overview.source,
        fetched_at=overview.fetched_at,
    ))
    db.commit()
    return {
        "status": "ok", "authority_score": overview.authority_score,
        "referring_domains": overview.referring_domains, "total_backlinks": overview.total_backlinks,
        "follow_links": overview.follow_links, "nofollow_links": overview.nofollow_links,
        "fetched_at": overview.fetched_at,
    }


@router.get("/projects/{project_id}/backlinks/records")
def list_backlink_records(project_id: int, filter: str = "active", db: Session = Depends(get_db)):
    """New/Lost tabs data (Task 5.2). filter: 'new' (first seen in the most
    recent pull), 'lost' (lost_at set), 'active' (currently live, default)."""
    if not db.get(models.Project, project_id):
        raise HTTPException(status_code=404, detail="Project not found")

    query = db.query(models.BacklinkRecord).filter(models.BacklinkRecord.project_id == project_id)
    if filter == "lost":
        query = query.filter(models.BacklinkRecord.lost_at.isnot(None)).order_by(models.BacklinkRecord.lost_at.desc())
    elif filter == "new":
        # "New" = first_seen_at matches the most recent pull's timestamp,
        # i.e. this run is the first time we've ever seen it.
        latest_pull = db.query(models.BacklinkRecord.first_seen_at).filter(
            models.BacklinkRecord.project_id == project_id
        ).order_by(models.BacklinkRecord.first_seen_at.desc()).first()
        if not latest_pull:
            return []
        query = query.filter(models.BacklinkRecord.first_seen_at == latest_pull[0]).order_by(models.BacklinkRecord.source_url)
    else:
        query = query.filter(models.BacklinkRecord.lost_at.is_(None)).order_by(models.BacklinkRecord.last_seen_at.desc())

    return [
        {
            "id": r.id, "source_url": r.source_url, "target_url": r.target_url,
            "anchor_text": r.anchor_text, "is_follow": r.is_follow,
            "first_seen_at": r.first_seen_at, "last_seen_at": r.last_seen_at, "lost_at": r.lost_at,
        }
        for r in query.limit(200).all()
    ]


PROJECT_SCHEDULABLE_JOB_TYPES = ("backlink_pull",)


@router.post("/projects/{project_id}/schedule/{job_type}")
def save_project_schedule(project_id: int, job_type: str, payload: schemas.CrawlSettingsIn, db: Session = Depends(get_db)):
    """Generic project-level schedule save for job types not already covered
    by save_crawl_settings (job_type='crawl') -- backlink_pull for now."""
    if job_type not in PROJECT_SCHEDULABLE_JOB_TYPES:
        raise HTTPException(status_code=400, detail=f"job_type must be one of {PROJECT_SCHEDULABLE_JOB_TYPES}")
    if not db.get(models.Project, project_id):
        raise HTTPException(status_code=404, detail="Project not found")

    schedule = (
        db.query(models.Schedule)
        .filter(models.Schedule.project_id == project_id, models.Schedule.job_type == job_type)
        .first()
    )
    if not schedule:
        schedule = models.Schedule(project_id=project_id, job_type=job_type)
        db.add(schedule)

    schedule.enabled = payload.enabled
    schedule.interval = payload.interval
    schedule.timezone = payload.timezone
    schedule.cron_expression = payload.cron_expression
    if schedule.next_run_at is None:
        from ..scheduler import compute_next_run_at
        schedule.next_run_at = compute_next_run_at(schedule)
    db.commit()
    db.refresh(schedule)
    return {
        "enabled": schedule.enabled, "interval": schedule.interval,
        "timezone": schedule.timezone, "cron_expression": schedule.cron_expression,
        "next_run_at": schedule.next_run_at,
    }


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


@router.get("/projects/{project_id}/pages/{page_id}/detail-json")
def page_detail_json(project_id: int, page_id: int, issue_id: int | None = None, db: Session = Depends(get_db)):
    """Same data as page_detail(), shaped as JSON so 'Fix on Page' can open an
    in-context modal (project_detail.html) instead of navigating to the
    separate page_detail.html view, which uses a different design system and
    reads as a broken/inconsistent page when reached from the new dashboard.

    issue_id (optional): the specific issue the user clicked "Fix on Page"
    from. When given, that issue is returned separately as "focused_issue"
    so the modal can show it first/highlighted; the rest go in
    "other_issues" as before."""
    page = db.get(models.Page, page_id)
    if not page or page.project_id != project_id:
        raise HTTPException(status_code=404, detail="Page not found")

    issues = page.issues
    non_title_issues = [issue for issue in issues if issue.category != "title"]

    def _issue_out(issue: models.Issue) -> dict:
        return {
            "id": issue.id,
            "category": issue.category,
            "rule": issue.rule,
            "severity": issue.severity,
            "message": issue.message,
            "suggestions": [
                {
                    "id": s.id,
                    "rank": s.rank,
                    "content": s.content,
                    "edited_content": s.edited_content,
                    "status": s.status,
                }
                for s in sorted(issue.suggestions, key=lambda s: s.rank)
            ],
        }

    focused_issue = None
    other_issues = non_title_issues
    if issue_id is not None:
        focused_issue = next((i for i in issues if i.id == issue_id), None)
        if focused_issue is not None:
            other_issues = [i for i in non_title_issues if i.id != issue_id]

    return {
        "id": page.id,
        "project_id": page.project_id,
        "url": page.url,
        "error": page.error,
        "score": audit.page_score(issues) if not page.error else None,
        "title": page.title,
        "wp_post_id": page.wp_post_id,
        "title_checklist": audit.title_checklist(page, issues),
        "focused_issue": _issue_out(focused_issue) if focused_issue else None,
        "other_issues": [_issue_out(i) for i in other_issues],
        "word_count": len((page.custom_content or "").split()),
        "crawled_ago": _time_ago(page.updated_at) if page.updated_at else None,
    }
