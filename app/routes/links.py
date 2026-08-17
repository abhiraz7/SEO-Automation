"""
Link Analyzer -- broken links, orphan pages, and internal/external link mix
for a project's most recent DataForSEO site-audit task. Sole data source is
DataForSEO's on_page/links endpoint (same task_id as the on-page crawl,
no separate cost) -- see dataforseo_onpage.fetch_task_links.

Matches Semrush/Ahrefs' standard "Internal Linking" report shape: broken
links (every internal/external link that 404s or errors), orphan pages
(zero inbound internal links from the crawl), and a dofollow/nofollow
split. Crawl-depth and "too many outgoing links" are left for a later
pass -- this is a v1 covering the two checks every competitor tool leads
with (broken links, orphans).
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import dataforseo_onpage, models
from ..database import get_db
from .settings import register_crawler_global

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
register_crawler_global(templates)


def _get_project(db: Session, project_id: int) -> models.Project:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def store_links_for_task(db: Session, project_id: int, task: models.OnPageTask) -> dict:
    """Fetches on_page/links for this task and replaces every PageLink row
    for it wholesale (a link DataForSEO no longer reports must not linger
    as a false positive). Called right after fetch_task_pages in
    onpage_semrush.check_site_audit -- same task_id, same "everything on
    runtime, no background poller" flow. Returns {"ok": True, "count": N}
    or {"ok": False, "error": ...}; never raises, since a links failure
    shouldn't roll back the page/issue ingestion that already succeeded."""
    result = dataforseo_onpage.fetch_task_links(task.dataforseo_task_id)
    if result.get("error"):
        return {"ok": False, "error": result["error"]}

    db.query(models.PageLink).filter(models.PageLink.onpage_task_id == task.id).delete(synchronize_session=False)
    count = 0
    for item in result.get("links") or []:
        # Only rows a link table can meaningfully show a row for -- meta/canonical
        # entries with no link_to (self-referential) add noise, not signal.
        url_to = item.get("link_to")
        url_from = item.get("link_from")
        if not url_to or not url_from:
            continue
        db.add(models.PageLink(
            project_id=project_id,
            onpage_task_id=task.id,
            url_from=url_from,
            url_to=url_to,
            link_type=item.get("type"),
            direction=item.get("direction"),
            dofollow=item.get("dofollow"),
            is_broken=bool(item.get("is_broken")),
            status_code=item.get("page_to_status_code"),
            anchor_text=item.get("text"),
            is_link_relation_conflict=bool(item.get("is_link_relation_conflict")),
        ))
        count += 1
    db.commit()
    return {"ok": True, "count": count}


def _latest_fetched_task(db: Session, project_id: int) -> models.OnPageTask | None:
    return (
        db.query(models.OnPageTask)
        .filter(models.OnPageTask.project_id == project_id, models.OnPageTask.status == "fetched")
        .order_by(models.OnPageTask.finished_at.desc())
        .first()
    )


@router.get("/projects/{project_id}/links")
def link_analyzer(project_id: int, request: Request, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    task = _latest_fetched_task(db, project_id)

    if not task:
        return templates.TemplateResponse(
            request, "links.html", {
                "project": project,
                "task": None,
                "has_data": False,
            }
        )

    links = db.query(models.PageLink).filter(models.PageLink.onpage_task_id == task.id).all()

    broken = [l for l in links if l.is_broken]
    internal = [l for l in links if l.direction == "internal"]
    external = [l for l in links if l.direction == "external"]
    dofollow_n = sum(1 for l in links if l.dofollow)
    nofollow_n = sum(1 for l in links if l.dofollow is False)
    conflicts = [l for l in links if l.is_link_relation_conflict]

    # Orphan pages: crawled pages that never appear as an internal link's
    # target from a DIFFERENT page -- i.e. nothing in the site links to them.
    crawled_urls = {
        p.url for p in db.query(models.Page).filter(
            models.Page.project_id == project_id, models.Page.onpage_task_id == task.id
        ).all()
    }
    linked_to = {l.url_to.rstrip("/") for l in internal}
    orphans = sorted(u for u in crawled_urls if u.rstrip("/") not in linked_to)

    # Inbound internal link count per target URL -- surfaces both orphans
    # (0) and over-linked pages, same "internal link count" view Semrush's
    # Internal Linking report leads with.
    inbound_counts: dict[str, int] = {}
    for l in internal:
        key = l.url_to.rstrip("/")
        inbound_counts[key] = inbound_counts.get(key, 0) + 1
    most_linked = sorted(inbound_counts.items(), key=lambda kv: kv[1], reverse=True)[:15]

    # Same "start at 100, subtract weighted penalties" shape as onpage_semrush's
    # health score -- broken links hurt more per-occurrence (0 link equity,
    # each one is a dead end a visitor/crawler hits), orphans hurt less (bad
    # for discovery, not actively broken). Computed here rather than inline in
    # the template: Jinja's filter/arithmetic precedence makes multi-term
    # expressions like this easy to get subtly wrong.
    link_health = 100
    if links:
        link_health = round(max(0, 100 - (len(broken) / len(links) * 100 * 3) - (len(orphans) * 2)))

    return templates.TemplateResponse(
        request, "links.html", {
            "project": project,
            "task": task,
            "has_data": True,
            "link_health": link_health,
            "total_links": len(links),
            "broken": sorted(broken, key=lambda l: l.status_code or 0, reverse=True),
            "broken_count": len(broken),
            "internal_count": len(internal),
            "external_count": len(external),
            "dofollow_count": dofollow_n,
            "nofollow_count": nofollow_n,
            "conflicts": conflicts,
            "orphans": orphans,
            "orphan_count": len(orphans),
            "most_linked": most_linked,
            "crawled_page_count": len(crawled_urls),
        }
    )
