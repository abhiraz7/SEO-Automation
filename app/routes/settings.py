"""
/settings -- lets the user pick which data provider (DataForSEO or SEMrush)
drives on-page audits AND backlinks (backlinks_provider.py reads this same
setting -- one switch, both tools follow it, per the "switched for all
tools" requirement). Exactly one is active at a time; toggling one on turns
the other off. Keyword Research is deliberately NOT governed by this switch
-- see backlinks_provider.py's module docstring for why.
"""
import os

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import ai_provider, api_keys, dataforseo_onpage, models, semrush, semrush_audit
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


def get_site_audit_cooldown_hours(db: Session) -> int:
    """No-arg-friendly getter for start_site_audit's cooldown check (see
    models.SiteAuditSettings). Defaults to 24h if the singleton row is
    somehow missing (fresh DB before its migration/first /settings visit)."""
    row = db.get(models.SiteAuditSettings, 1)
    return row.cooldown_hours if row else 24


def register_crawler_global(templates_env: Jinja2Templates) -> None:
    """Every Jinja2Templates instance across routes/ gets its own Environment,
    so this must be called once per instance for sidebar.html's
    {{ crawler_enabled() }} check to work on every page."""
    templates_env.env.globals["crawler_enabled"] = crawler_enabled_flag


register_crawler_global(templates)


LOG_LEVELS = ("WARNING", "ERROR", "CRITICAL")
LOG_DEFAULT_LIMIT, LOG_MAX_LIMIT = 200, 1000


def _query_logs(db: Session, level: str = "all", q: str = "", limit: int = LOG_DEFAULT_LIMIT):
    """(rows newest-first, total matching). `level` is all or one of LOG_LEVELS;
    `q` is a case-insensitive substring over logger + message."""
    query = db.query(models.AppLog)
    if level in LOG_LEVELS:
        query = query.filter(models.AppLog.level == level)
    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        query = query.filter(models.AppLog.message.ilike(like) | models.AppLog.logger.ilike(like))
    total = query.count()
    limit = max(1, min(int(limit), LOG_MAX_LIMIT))
    return query.order_by(models.AppLog.id.desc()).limit(limit).all(), total


def _log_block(row: models.AppLog) -> str:
    stamp = row.created_at.strftime("%Y-%m-%d %H:%M:%S UTC") if row.created_at else "?"
    return f"{stamp}  {row.level}  {row.logger}\n{row.message}"


@router.get("/settings/logs.txt")
def download_logs(level: str = "all", q: str = "", limit: int = LOG_MAX_LIMIT, db: Session = Depends(get_db)):
    """The raw (already credential-redacted) log as plain text, newest first."""
    rows, _ = _query_logs(db, level, q, limit)
    return PlainTextResponse("\n\n".join(_log_block(r) for r in rows) or "(no log entries)", media_type="text/plain; charset=utf-8")


@router.post("/settings/logs/clear")
def clear_logs(db: Session = Depends(get_db)):
    db.query(models.AppLog).delete(synchronize_session=False)
    db.commit()
    return RedirectResponse(url="/settings#logs", status_code=303)


@router.get("/settings")
def settings_page(request: Request, level: str = "all", q: str = "", limit: int = LOG_DEFAULT_LIMIT, db: Session = Depends(get_db)):
    active = get_active_provider(db)
    log_rows, log_total = _query_logs(db, level, q, limit)

    dataforseo_status = {
        "configured": dataforseo_onpage.is_configured(),
        "detail": "Credentials set — verified working today." if dataforseo_onpage.is_configured() else "DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD not set.",
    }
    if semrush_audit.is_configured():
        health = semrush.health_check()
        semrush_status = {"configured": True, "detail": health.get("detail", "")}
    else:
        semrush_status = {"configured": False, "detail": "SEMRUSH_API_KEY not set."}

    active_ai = ai_provider.get_active_ai_provider(db)
    gemini_status = {
        "configured": bool(os.environ.get("GEMENI_KEY")),
        "detail": "GEMENI_KEY set." if os.environ.get("GEMENI_KEY") else "GEMENI_KEY not set.",
    }
    claude_status = {
        "configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "detail": "ANTHROPIC_API_KEY set." if os.environ.get("ANTHROPIC_API_KEY") else "ANTHROPIC_API_KEY not set.",
    }

    return templates.TemplateResponse(
        request, "settings.html", {
            "active_provider": active,
            "dataforseo_status": dataforseo_status,
            "semrush_status": semrush_status,
            "active_ai_provider": active_ai,
            "gemini_status": gemini_status,
            "claude_status": claude_status,
            "site_audit_cooldown_hours": get_site_audit_cooldown_hours(db),
            "api_keys": api_keys.key_status(),
            "log_entries": [{"row": r, "text": _log_block(r)} for r in log_rows],
            "log_total": log_total,
            "log_level": level if level in LOG_LEVELS else "all",
            "log_q": q,
            "log_levels": LOG_LEVELS,
        }
    )


@router.post("/settings/site-audit/cooldown")
def save_site_audit_cooldown(cooldown_hours: int = Form(...), db: Session = Depends(get_db)):
    cooldown_hours = max(0, min(cooldown_hours, 24 * 30))  # 0 = disabled, capped at 30 days against fat-fingering
    row = db.get(models.SiteAuditSettings, 1)
    if not row:
        row = models.SiteAuditSettings(id=1)
        db.add(row)
    row.cooldown_hours = cooldown_hours
    db.commit()
    return RedirectResponse(url="/settings", status_code=303)


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


@router.post("/settings/ai-provider/{provider}/activate")
def activate_ai_provider(provider: str, db: Session = Depends(get_db)):
    if provider not in ai_provider.AI_PROVIDERS:
        return RedirectResponse(url="/settings", status_code=303)

    for name in ai_provider.AI_PROVIDERS:
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
