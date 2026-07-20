"""
WordPress connection + deploy/rollback routes (Tasks 3.2-3.5).
"""
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models, wordpress
from ..database import get_db

router = APIRouter()

# Politeness delay between resolve_post_id_by_url calls when resolving a
# whole project's pages in one pass -- each call is 1-2 HTTP requests to the
# target site (plus one more for the homepage's get_options), so a 25-page
# project without this would fire a burst of 25-50 requests at once.
_RESOLVE_PAGE_DELAY_SECONDS = 0.3


# ── Field deploy registry (Task 3.5) ─────────────────────────────────────
# One entry per deployable Issue.category: how to READ the current value
# from WordPress (for before_value) and how to WRITE the new one. Adding a
# new field type is one entry here, nothing else changes in deploy/rollback.
#
# Real category values, confirmed from audit.py's _issue() calls: title,
# meta_description, h1, h2, image_alt, schema, canonical, opengraph,
# twitter, lang, content. Wired here:
#   meta_description -> Yoast SEO meta description (yoast_set_meta)
#   title             -> Yoast SEO <title> tag, NOT the WP post title (also
#                        called "title" but a DIFFERENT WordPress field --
#                        yoast_set_meta(seo_title=...) is deliberate here)
#   h1                -> the WordPress post title itself (what themes render
#                        as the H1 in the default template) -- update_post
#
# NOT wired: image_alt. The plugin's update_media_meta tool takes a
# media_id, but our deploy contract (DeployIn.wp_post_id) only carries a
# post_id -- a post and its images are different WordPress objects with
# different IDs. Deploying alt text needs a media_id lookup path this repo
# doesn't have yet (Page/CrawlSnapshot store alt text strings, not the
# WordPress media library IDs they came from). Flagging this rather than
# building a broken mapping; see AgentLog for what a real fix needs.

def _read_meta_description(site_url: str, token: str, wp_post_id: int) -> wordpress.WordPressResult:
    return wordpress.get_yoast_meta(site_url, token, wp_post_id)


def _write_meta_description(site_url: str, token: str, wp_post_id: int, value: str) -> wordpress.WordPressResult:
    return wordpress.set_yoast_meta(site_url, token, wp_post_id, meta_description=value)


def _read_seo_title(site_url: str, token: str, wp_post_id: int) -> wordpress.WordPressResult:
    return wordpress.get_yoast_meta(site_url, token, wp_post_id)


def _write_seo_title(site_url: str, token: str, wp_post_id: int, value: str) -> wordpress.WordPressResult:
    return wordpress.set_yoast_meta(site_url, token, wp_post_id, seo_title=value)


def _read_post_title(site_url: str, token: str, wp_post_id: int) -> wordpress.WordPressResult:
    return wordpress.get_post(site_url, token, wp_post_id)


def _write_post_title(site_url: str, token: str, wp_post_id: int, value: str) -> wordpress.WordPressResult:
    return wordpress.update_post_content(site_url, token, wp_post_id, title=value)


FIELD_DEPLOYERS = {
    "meta_description": {
        "read": _read_meta_description,
        "read_key": "meta_description",  # key inside the read result's .data to extract before_value
        "write": _write_meta_description,
        "tool": "yoast_set_meta",
    },
    "title": {
        "read": _read_seo_title,
        "read_key": "seo_title",
        "write": _write_seo_title,
        "tool": "yoast_set_meta",
    },
    "h1": {
        "read": _read_post_title,
        "read_key": "title",
        "write": _write_post_title,
        "tool": "update_post",
    },
}


class WordPressConnectionIn(BaseModel):
    site_url: str
    api_token: str
    is_staging: bool = True


def _connection_out(c: models.WordPressConnection) -> dict:
    return {
        "id": c.id,
        "project_id": c.project_id,
        "site_url": c.site_url,
        "is_staging": c.is_staging,
        "last_verified_at": c.last_verified_at,
        "last_verify_ok": c.last_verify_ok,
        # api_token intentionally never returned -- write-only from the client's
        # perspective once saved, same principle as a password field.
    }


@router.get("/projects/{project_id}/wordpress")
def get_wordpress_connection(project_id: int, db: Session = Depends(get_db)):
    conn = db.query(models.WordPressConnection).filter(models.WordPressConnection.project_id == project_id).first()
    if not conn:
        return {"connected": False}
    return {"connected": True, **_connection_out(conn)}


@router.post("/projects/{project_id}/wordpress")
def save_wordpress_connection(project_id: int, payload: WordPressConnectionIn, db: Session = Depends(get_db)):
    if not db.get(models.Project, project_id):
        raise HTTPException(status_code=404, detail="Project not found")

    site_url = payload.site_url.strip().rstrip("/")
    if not site_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="site_url must start with http:// or https://")
    token = payload.api_token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="api_token is required")

    try:
        encrypted = wordpress.encrypt_token(token)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    conn = db.query(models.WordPressConnection).filter(models.WordPressConnection.project_id == project_id).first()
    if not conn:
        conn = models.WordPressConnection(project_id=project_id)
        db.add(conn)

    conn.site_url = site_url
    conn.api_token = encrypted
    conn.is_staging = payload.is_staging
    conn.last_verified_at = None
    conn.last_verify_ok = None
    db.commit()
    db.refresh(conn)
    return _connection_out(conn)


@router.post("/projects/{project_id}/wordpress/test")
def test_wordpress_connection(project_id: int, db: Session = Depends(get_db)):
    conn = db.query(models.WordPressConnection).filter(models.WordPressConnection.project_id == project_id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="No WordPress connection saved for this project yet")

    try:
        token = wordpress.decrypt_token(conn.api_token)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    result = wordpress.test_connection(conn.site_url, token)
    conn.last_verified_at = datetime.now(timezone.utc)
    conn.last_verify_ok = result.ok
    db.commit()

    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error or "Connection test failed")

    # A connection just (re-)verified successfully means every page in this
    # project can now potentially resolve its WordPress post ID -- do it now
    # rather than making every page wait for its next individual crawl.
    resolve_summary = _resolve_all_pages(db, project_id, conn, token)
    return {"ok": True, "site": result.data, "resolved_pages": resolve_summary}


@router.post("/projects/{project_id}/wordpress/resolve-pages")
def resolve_wordpress_pages(project_id: int, db: Session = Depends(get_db)):
    """Bulk-resolves wp_post_id for every page in this project that doesn't
    have one yet. Manual trigger for the 'Resolve WordPress IDs' button --
    the same work also happens automatically after Test Connection passes,
    but pages crawled after that (or added since) won't have been covered
    yet, so this lets a user force a fresh pass on demand."""
    conn, token = _connected_or_error(db, project_id)
    return _resolve_all_pages(db, project_id, conn, token)


@router.post("/projects/{project_id}/pages/{page_id}/resolve-wp-post")
def resolve_single_page_wp_post(project_id: int, page_id: int, db: Session = Depends(get_db)):
    """Single-page version for the Fix on Page modal's 'Resolve now' inline
    action -- same underlying resolver, scoped to one page so it's instant
    instead of walking the whole project."""
    page = db.get(models.Page, page_id)
    if not page or page.project_id != project_id:
        raise HTTPException(status_code=404, detail="Page not found")
    conn, token = _connected_or_error(db, project_id)
    summary = _resolve_all_pages(db, project_id, conn, token, only_page_id=page_id)
    db.refresh(page)
    return {**summary, "wp_post_id": page.wp_post_id, "wp_post_type": page.wp_post_type}


# ── Deploy / rollback (Tasks 3.3-3.5) ────────────────────────────────────

class DeployIn(BaseModel):
    # Optional: Page.wp_post_id is resolved automatically during crawl (see
    # routes/crawl.py._maybe_resolve_wp_post_id, via WordPress's core REST
    # API) and used here when the caller doesn't supply one. Still
    # overridable/settable manually for the cases resolution can't handle
    # (homepage, ambiguous slug, site unreachable at crawl time). If
    # supplied, it's persisted onto the page row so it's never asked again.
    wp_post_id: int | None = None


def _resolve_wp_post_id(db: Session, suggestion: models.Suggestion, conn: models.WordPressConnection, token: str, explicit: int | None) -> int:
    if explicit is not None:
        if explicit <= 0:
            raise HTTPException(status_code=400, detail="wp_post_id must be a positive integer.")
        # A manually-supplied ID is real signal -- remember it on the page so
        # this exact question is never asked again for the same page.
        page = db.get(models.Page, suggestion.page_id)
        if page:
            page.wp_post_id = explicit
            db.commit()
        return explicit

    page = db.get(models.Page, suggestion.page_id)
    if page and page.wp_post_id:
        return page.wp_post_id

    # Cached value missing/stale -- try a live resolve before giving up, in
    # case the connection was only just saved after this page was crawled.
    if page:
        result = wordpress.resolve_post_id_by_url(conn.site_url, page.url, token=token)
        if result.ok:
            page.wp_post_id = result.data.get("post_id")
            page.wp_post_type = result.data.get("post_type")
            db.commit()
            return page.wp_post_id
        if result.data.get("reason") == "homepage_is_post_archive":
            # Not a "give me a number" situation -- no numeric ID would fix
            # this, so a distinct status lets the UI show the real reason
            # instead of the generic manual-entry prompt.
            raise HTTPException(
                status_code=422,
                detail={"message": result.error, "reason": "homepage_is_post_archive"},
            )

    raise HTTPException(
        status_code=400,
        detail="Could not determine the WordPress post ID for this page automatically -- pass wp_post_id explicitly. This will be saved for future deploys.",
    )


def _resolve_all_pages(db: Session, project_id: int, conn: models.WordPressConnection, token: str, only_page_id: int | None = None) -> dict:
    """Shared by POST /wordpress/resolve-pages (bulk) and the auto-trigger
    right after a connection is saved/re-verified, plus the single-page
    'Resolve now' modal action (only_page_id). Politely rate-limited --
    see _RESOLVE_PAGE_DELAY_SECONDS -- since this can fire one to a few
    HTTP requests per page against the target site."""
    query = db.query(models.Page).filter(models.Page.project_id == project_id, models.Page.wp_post_id.is_(None))
    if only_page_id is not None:
        query = query.filter(models.Page.id == only_page_id)
    pages = query.all()

    resolved = 0
    failures = []
    for i, page in enumerate(pages):
        if i > 0:
            time.sleep(_RESOLVE_PAGE_DELAY_SECONDS)
        result = wordpress.resolve_post_id_by_url(conn.site_url, page.url, token=token)
        if result.ok:
            page.wp_post_id = result.data.get("post_id")
            page.wp_post_type = result.data.get("post_type")
            resolved += 1
        else:
            failures.append({"url": page.url, "reason": result.data.get("reason") or result.error})
    db.commit()
    return {"resolved": resolved, "failed": len(failures), "failures": failures}


def _revision_out(r: models.SuggestionRevision) -> dict:
    return {
        "id": r.id,
        "suggestion_id": r.suggestion_id,
        "field_name": r.field_name,
        "before_value": r.before_value,
        "after_value": r.after_value,
        "wp_post_id": r.wp_post_id,
        "deployed_via": r.deployed_via,
        "deployed_at": r.deployed_at,
        "rolled_back_at": r.rolled_back_at,
    }


def _connected_or_error(db: Session, project_id: int) -> tuple[models.WordPressConnection, str]:
    conn = db.query(models.WordPressConnection).filter(models.WordPressConnection.project_id == project_id).first()
    if not conn:
        raise HTTPException(status_code=400, detail="No WordPress connection saved for this project.")
    if not conn.last_verify_ok:
        raise HTTPException(status_code=400, detail="WordPress connection has not passed a Test Connection check yet.")
    try:
        token = wordpress.decrypt_token(conn.api_token)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return conn, token


# Category -> Page column that "Current" (current_value_for in audit.py)
# reads for that category. After a successful deploy we know exactly what
# we just wrote, so we update our own copy immediately rather than leaving
# "Current" showing the pre-deploy value until the next crawl/re-audit.
# h1 is stored as a list on Page (crawler can see multiple H1s) -- a deploy
# only ever supplies one value, so it replaces the list with a single-item
# list; this is an approximation for pages that genuinely had >1 H1, but a
# stale display would be a worse default than an accurate single value.
_PAGE_FIELD_FOR_CATEGORY = {"title": "title", "meta_description": "meta_description", "h1": "h1"}


def _apply_deploy_to_page(db: Session, page_id: int, field_name: str, new_value: str) -> None:
    page = db.get(models.Page, page_id)
    if not page:
        return
    page_field = _PAGE_FIELD_FOR_CATEGORY.get(field_name)
    if not page_field:
        return
    if page_field == "h1":
        page.h1 = [new_value]
    else:
        setattr(page, page_field, new_value)


@router.post("/suggestions/{suggestion_id}/deploy")
def deploy_suggestion(suggestion_id: int, payload: DeployIn, db: Session = Depends(get_db)):
    """Deploys an accepted/edited suggestion's value to WordPress. Reads the
    CURRENT value from the live site first (real before_value, not assumed),
    then writes. A SuggestionRevision row -- and the suggestion's
    status='deployed' -- are only written on a successful WRITE; a failed
    read or write leaves the suggestion's status untouched and writes no
    revision, so a revision existing always means the deploy really
    happened."""
    suggestion = db.get(models.Suggestion, suggestion_id)
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    if suggestion.status not in ("accepted", "edited"):
        raise HTTPException(status_code=409, detail=f"Suggestion must be accepted or edited first (current status: {suggestion.status}).")

    issue = db.get(models.Issue, suggestion.issue_id)
    field_name = issue.category if issue else None
    deployer = FIELD_DEPLOYERS.get(field_name)
    if not deployer:
        raise HTTPException(status_code=400, detail=f"No deploy support yet for field type {field_name!r}.")

    conn, token = _connected_or_error(db, suggestion.project_id)
    wp_post_id = _resolve_wp_post_id(db, suggestion, conn, token, payload.wp_post_id)

    read_result = deployer["read"](conn.site_url, token, wp_post_id)
    if not read_result.ok and read_result.status == "error":
        raise HTTPException(status_code=502, detail=f"Could not read current value from WordPress: {read_result.error}")
    before_value = read_result.data.get(deployer["read_key"]) if read_result.ok else None

    new_value = suggestion.edited_content or suggestion.content
    write_result = deployer["write"](conn.site_url, token, wp_post_id, new_value)
    if not write_result.ok:
        raise HTTPException(status_code=502, detail=f"Deploy failed: {write_result.error or 'unknown error'}")

    revision = models.SuggestionRevision(
        suggestion_id=suggestion.id,
        project_id=suggestion.project_id,
        field_name=field_name,
        before_value=before_value,
        after_value=new_value,
        wp_post_id=wp_post_id,
        deployed_via=deployer["tool"],
        deploy_result_raw=write_result.data,
    )
    db.add(revision)
    suggestion.status = "deployed"
    suggestion.deployed_at = datetime.now(timezone.utc)

    # Only one suggestion can actually be "live" for a given issue at a
    # time -- demote any sibling that was previously deployed (this is the
    # same status transition rollback already uses) so the UI never shows
    # more than one "deployed" card per issue when only the newest write is
    # really on the site.
    superseded = (
        db.query(models.Suggestion)
        .filter(
            models.Suggestion.issue_id == suggestion.issue_id,
            models.Suggestion.id != suggestion.id,
            models.Suggestion.status == "deployed",
        )
        .all()
    )
    for s in superseded:
        s.status = "accepted"

    _apply_deploy_to_page(db, suggestion.page_id, field_name, new_value)

    db.commit()
    db.refresh(revision)
    return _revision_out(revision)


@router.post("/revisions/{revision_id}/rollback")
def rollback_revision(revision_id: int, db: Session = Depends(get_db)):
    """Writes the revision's before_value back to WordPress, then marks the
    revision rolled back and the suggestion 'accepted' again (not 'pending'
    -- the human decision to use this suggestion still stands, only the live
    deploy is being undone)."""
    revision = db.get(models.SuggestionRevision, revision_id)
    if not revision:
        raise HTTPException(status_code=404, detail="Revision not found")
    if revision.rolled_back_at:
        raise HTTPException(status_code=409, detail="Already rolled back.")

    deployer = FIELD_DEPLOYERS.get(revision.field_name)
    if not deployer:
        raise HTTPException(status_code=400, detail=f"No deploy support for field type {revision.field_name!r} -- cannot roll back.")

    conn, token = _connected_or_error(db, revision.project_id)

    write_result = deployer["write"](conn.site_url, token, revision.wp_post_id, revision.before_value or "")
    if not write_result.ok:
        raise HTTPException(status_code=502, detail=f"Rollback failed: {write_result.error or 'unknown error'}")

    revision.rolled_back_at = datetime.now(timezone.utc)
    suggestion = db.get(models.Suggestion, revision.suggestion_id)
    if suggestion and suggestion.status == "deployed":
        suggestion.status = "accepted"
    db.commit()
    return _revision_out(revision)


@router.get("/projects/{project_id}/revisions")
def list_revisions(project_id: int, db: Session = Depends(get_db)):
    """Revision History panel data (Task 3.4)."""
    rows = (
        db.query(models.SuggestionRevision)
        .filter(models.SuggestionRevision.project_id == project_id)
        .order_by(models.SuggestionRevision.deployed_at.desc())
        .all()
    )
    return [_revision_out(r) for r in rows]
