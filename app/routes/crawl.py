from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import crawler, models
from ..database import get_db

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

PAGE_FIELDS = [
    "status_code", "error", "title", "meta_description", "meta_keywords",
    "h1", "h2", "heading_structure", "image_alts", "domain_schema", "page_schemas",
    "canonical", "og_title", "og_description", "og_url",
    "twitter_title", "twitter_description", "twitter_site", "twitter_card",
    "lang", "custom_content",
]


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
    return page


def _get_project(db: Session, project_id: int) -> models.Project:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _pages_response(request: Request, db: Session, project: models.Project):
    pages = (
        db.query(models.Page)
        .filter(models.Page.project_id == project.id)
        .order_by(models.Page.url)
        .all()
    )
    return templates.TemplateResponse(
        request, "partials/pages_table.html", {"pages": pages, "project": project}
    )


@router.post("/projects/{project_id}/crawl")
def crawl_project(project_id: int, request: Request, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    for data in crawler.crawl_site(project.base_url):
        upsert_page(db, project_id, data)
    return _pages_response(request, db, project)


@router.post("/projects/{project_id}/crawl-single")
def crawl_single(
    project_id: int,
    request: Request,
    url: str = Form(None),
    db: Session = Depends(get_db),
):
    project = _get_project(db, project_id)
    data = crawler.crawl_single_page(url or project.base_url)
    upsert_page(db, project_id, data)
    return _pages_response(request, db, project)
