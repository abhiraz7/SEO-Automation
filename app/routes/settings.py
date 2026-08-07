"""
/settings -- lets the user pick which on-page data provider (DataForSEO or
SEMrush) drives /onpage-semrush. Exactly one is active at a time; toggling
one on turns the other off.
"""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import dataforseo_onpage, models, semrush, semrush_audit
from ..database import SessionLocal, get_db

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

PROVIDERS = ("dataforseo", "semrush")


def get_active_provider(db: Session) -> str:
    row = (
        db.query(models.ProviderSetting)
        .filter(models.ProviderSetting.provider.in_(PROVIDERS), models.ProviderSetting.enabled.is_(True))
        .first()
    )
    return row.provider if row else "dataforseo"


def crawler_enabled_flag() -> bool:
    """No-arg version of is_crawler_enabled for use as a Jinja global --
    sidebar.html is included on every page (via base.html), so it needs a
    way to check this without every route threading crawler_enabled through
    its own template context. Opens its own short-lived session since
    templates render outside any route's Depends(get_db)."""
    db = SessionLocal()
    try:
        return is_crawler_enabled(db)
    finally:
        db.close()


def is_crawler_enabled(db: Session) -> bool:
    row = db.get(models.CrawlerSettings, 1)
    return row.enabled if row else True


def register_crawler_global(templates_env: Jinja2Templates) -> None:
    """Every Jinja2Templates instance across routes/ gets its own Environment,
    so this must be called once per instance for sidebar.html's
    {{ crawler_enabled() }} check to work on every page."""
    templates_env.env.globals["crawler_enabled"] = crawler_enabled_flag


register_crawler_global(templates)


@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db)):
    active = get_active_provider(db)

    dataforseo_status = {
        "configured": dataforseo_onpage.is_configured(),
        "detail": "Credentials set — verified working today." if dataforseo_onpage.is_configured() else "DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD not set.",
    }
    if semrush_audit.is_configured():
        health = semrush.health_check()
        semrush_status = {"configured": True, "detail": health.get("detail", "")}
    else:
        semrush_status = {"configured": False, "detail": "SEMRUSH_API_KEY not set."}

    return templates.TemplateResponse(
        request, "settings.html", {
            "active_provider": active,
            "dataforseo_status": dataforseo_status,
            "semrush_status": semrush_status,
        }
    )


@router.post("/settings/provider/{provider}/activate")
def activate_provider(provider: str, db: Session = Depends(get_db)):
    if provider not in PROVIDERS:
        return RedirectResponse(url="/settings", status_code=303)

    for name in PROVIDERS:
        row = db.query(models.ProviderSetting).filter(models.ProviderSetting.provider == name).first()
        if not row:
            row = models.ProviderSetting(provider=name)
            db.add(row)
        row.enabled = (name == provider)
    db.commit()
    return RedirectResponse(url="/settings", status_code=303)


# ── Hidden crawler kill switch -- deliberately not linked anywhere in the
# sidebar or /settings. Reachable only by typing /settings/crawler directly. ──

@router.get("/settings/crawler")
def crawler_settings_page(request: Request, db: Session = Depends(get_db)):
    row = db.get(models.CrawlerSettings, 1)
    if not row:
        row = models.CrawlerSettings(id=1, enabled=True)
        db.add(row)
        db.commit()
        db.refresh(row)

    crawler_project_count = (
        db.query(models.Project).filter(models.Project.project_type == "manual").count()
    )
    queued_crawl_jobs = (
        db.query(models.Job).filter(models.Job.job_type == "crawl", models.Job.status == "queued").count()
    )
    active_crawl_schedules = (
        db.query(models.Schedule)
        .filter(models.Schedule.job_type == "crawl", models.Schedule.enabled.is_(True))
        .count()
    )

    return templates.TemplateResponse(
        request, "settings_crawler.html", {
            "enabled": row.enabled,
            "updated_at": row.updated_at,
            "crawler_project_count": crawler_project_count,
            "queued_crawl_jobs": queued_crawl_jobs,
            "active_crawl_schedules": active_crawl_schedules,
        }
    )


@router.post("/settings/crawler/toggle")
def toggle_crawler(enabled: bool = Form(...), db: Session = Depends(get_db)):
    row = db.get(models.CrawlerSettings, 1)
    if not row:
        row = models.CrawlerSettings(id=1)
        db.add(row)
    row.enabled = enabled
    db.commit()
    return RedirectResponse(url="/settings/crawler", status_code=303)
