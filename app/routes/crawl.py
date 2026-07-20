from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import crawler, models, wordpress
from ..database import get_db

router = APIRouter()

PAGE_FIELDS = [
    "status_code", "error", "title", "meta_description", "meta_keywords",
    "h1", "h2", "heading_structure", "image_alts", "domain_schema", "page_schemas",
    "canonical", "og_title", "og_description", "og_url",
    "twitter_title", "twitter_description", "twitter_site", "twitter_card",
    "lang", "custom_content", "markdown", "fit_markdown", "internal_links",
]


def _maybe_resolve_wp_post_id(db: Session, project_id: int, page: models.Page) -> None:
    """Best-effort: if this project has a WordPress connection saved, try to
    resolve this page's WP post ID from its URL right now, during crawl/
    re-audit, so 'Deploy to WordPress' later doesn't need to ask for a
    numeric ID. Purely additive and never fatal -- any failure (no
    connection, no match, site unreachable) just leaves wp_post_id as it
    was; it never blocks or fails the crawl itself."""
    conn = (
        db.query(models.WordPressConnection)
        .filter(models.WordPressConnection.project_id == project_id)
        .first()
    )
    if not conn:
        return
    try:
        token = wordpress.decrypt_token(conn.api_token)
    except Exception:
        token = None
    try:
        result = wordpress.resolve_post_id_by_url(conn.site_url, page.url, token=token)
    except Exception:
        return
    if result.ok:
        page.wp_post_id = result.data.get("post_id")
        page.wp_post_type = result.data.get("post_type")
        db.commit()


def upsert_page(db: Session, project_id: int, data: dict) -> models.Page:
    page = (
        db.query(models.Page)
        .filter(models.Page.project_id == project_id, models.Page.url == data["url"])
        .first()
    )
    if not page:
        page = models.Page(project_id=project_id, url=data["url"])
        db.add(page)

    for field in PAGE_FIELDS:
        setattr(page, field, data.get(field))

    db.commit()
    db.refresh(page)

    db.add(models.CrawlSnapshot(project_id=project_id, page_id=page.id, url=page.url, data=data))
    db.commit()

    _maybe_resolve_wp_post_id(db, project_id, page)
    return page


def _get_project(db: Session, project_id: int) -> models.Project:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project



@router.post("/projects/{project_id}/crawl")
def crawl_project(project_id: int, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    for data in crawler.crawl_site(project.base_url):
        upsert_page(db, project_id, data)
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/crawl-single")
def crawl_single(
    project_id: int,
    url: str = Form(None),
    db: Session = Depends(get_db),
):
    project = _get_project(db, project_id)
    data = crawler.crawl_single_page(url or project.base_url)
    upsert_page(db, project_id, data)
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)
