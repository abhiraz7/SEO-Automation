"""
WordPress connection + deploy/rollback routes (Tasks 3.2-3.5).
"""
import re
import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models, wordpress
from ..database import SessionLocal, get_db

router = APIRouter()

# The plugin lives in its own repo (github.com/abhiraz7/AI-SEO-Connector) and is
# released there, so this platform never carries a copy that can drift out of
# date (the old bundled zip was the stale VtechSEO Agent 1.0.0 plugin).
PLUGIN_REPO = "abhiraz7/AI-SEO-Connector"
PLUGIN_ASSET = "ai-seo-connector.zip"
PLUGIN_LATEST_API = f"https://api.github.com/repos/{PLUGIN_REPO}/releases/latest"
# Fallback when GitHub's API can't be reached: still gets the user the newest
# zip, just under the unversioned asset name.
PLUGIN_DOWNLOAD_URL = f"https://github.com/{PLUGIN_REPO}/releases/latest/download/{PLUGIN_ASSET}"

# Unauthenticated GitHub API calls are limited to 60/hour per IP, so remember
# the latest release for a few minutes instead of asking on every click.
_PLUGIN_RELEASE_CACHE_SECONDS = 600
_plugin_release_cache: dict = {"at": 0.0, "value": None}

# Only ever put a plain version number in a Content-Disposition filename.
_SAFE_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:[-.][0-9A-Za-z]+)*$")


def _latest_plugin_release() -> tuple[str, str] | None:
    """(version, asset download URL) of the newest AI SEO Connector release, or
    None if it can't be determined. Never raises: a slow or unreachable GitHub
    just means the caller falls back to the plain redirect."""
    now = time.time()
    if _plugin_release_cache["value"] and now - _plugin_release_cache["at"] < _PLUGIN_RELEASE_CACHE_SECONDS:
        return _plugin_release_cache["value"]
    try:
        resp = httpx.get(PLUGIN_LATEST_API, headers={"Accept": "application/vnd.github+json"}, timeout=10)
        resp.raise_for_status()
        release = resp.json()
        version = str(release.get("tag_name", "")).lstrip("v")
        if not _SAFE_VERSION.match(version):
            return None
        asset_url = next(
            (a.get("browser_download_url") for a in release.get("assets", []) if a.get("name") == PLUGIN_ASSET),
            None,
        )
        if not asset_url:
            return None
    except (httpx.HTTPError, ValueError, AttributeError):
        return None
    _plugin_release_cache.update(at=now, value=(version, asset_url))
    return version, asset_url


# The old path is kept so existing links and bookmarks still land on the plugin.
@router.get("/downloads/ai-seo-connector")
@router.get("/downloads/vtechseo-agent")
def download_wp_plugin():
    """Serves the latest AI SEO Connector release zip as
    ai-seo-connector-<version>.zip -- the connection drawer links here so a
    user can install the plugin before saving a connection. The file is fetched
    from the plugin's GitHub release and re-served with a versioned filename
    (a plain redirect can't rename it: the browser would save the release
    asset's own unversioned name). If GitHub can't be reached or the release
    has no matching asset, falls back to redirecting to the latest release's
    zip so the download still works. The plugin is scoped to content/SEO/media/
    site-info only: no page-builder control, no plugin management, no PHP
    execution."""
    release = _latest_plugin_release()
    if release:
        version, asset_url = release
        try:
            zip_resp = httpx.get(asset_url, follow_redirects=True, timeout=30)
            zip_resp.raise_for_status()
        except httpx.HTTPError:
            return RedirectResponse(PLUGIN_DOWNLOAD_URL, status_code=302)
        return Response(
            content=zip_resp.content,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="ai-seo-connector-{version}.zip"'},
        )
    return RedirectResponse(PLUGIN_DOWNLOAD_URL, status_code=302)

# Politeness delay between resolve_post_id_by_url calls when resolving a
# whole project's pages in one pass -- each call is 1-2 HTTP requests to the
# target site (plus one more for the homepage's get_options), so a 25-page
# project without this would fire a burst of 25-50 requests at once.
_RESOLVE_PAGE_DELAY_SECONDS = 0.3

# In-flight guard for the background resolve sweep, keyed by project_id --
# same shape of fix as onpage_semrush.py's start_site_audit in-flight check
# (born from a real incident there: a double-clicked button with no guard
# fired 13 billed crawls of the same pages in 4 seconds). Without this,
# several people opening "Test connection" for the same project from
# different browsers/devices within the same few minutes each schedule
# their own full page-resolution sweep -- duplicate HTTP request bursts
# against the target site (risking the client's own rate-limiter/WAF) and
# concurrent SQLite writes to the same Page rows (this app's DB only
# tolerates one writer at a time -- see AgentLog's stuck-task fix for the
# same underlying constraint). Process-local only: covers the common
# single-process deployment this app currently runs as; would need a
# DB-backed flag instead if ever run behind multiple worker processes.
_active_resolve_sweeps: set[int] = set()


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
#   twitter           -> Yoast SEO Twitter Card title (yoast_set_meta(twitter_title=...)),
#                        the free-text field the "Twitter card meta tag is missing"
#                        suggestion actually fixes -- twitter_card/twitter_image are
#                        not AI-suggestable text, so only twitter_title is wired
#   canonical         -> Yoast SEO canonical URL (yoast_set_meta(canonical_url=...))
#   opengraph         -> Yoast SEO OG title (yoast_set_meta(og_title=...)) -- same
#                        one-field-of-several precedent as twitter_title; og_description
#                        isn't wired for the same reason twitter_description isn't
#
# NOT wired: h2, schema, lang, content, security. h2/schema/lang/content have no
# single WordPress write path that isn't already covered by h1/meta_description
# deploys or a full post-content edit; security checks (SSL, robots.txt) aren't
# something a suggestion can fix by writing one field. image_alt specifically:
# the plugin's update_media_meta tool takes a media_id, but our deploy contract
# (DeployIn.wp_post_id) only carries a post_id -- a post and its images are
# different WordPress objects with different IDs. Deploying alt text needs a
# media_id lookup path this repo doesn't have yet (Page/CrawlSnapshot store alt
# text strings, not the WordPress media library IDs they came from). Flagging
# this rather than building a broken mapping; see AgentLog for what a real fix needs.

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


def _read_twitter_title(site_url: str, token: str, wp_post_id: int) -> wordpress.WordPressResult:
    return wordpress.get_yoast_meta(site_url, token, wp_post_id)


def _write_twitter_title(site_url: str, token: str, wp_post_id: int, value: str) -> wordpress.WordPressResult:
    return wordpress.set_yoast_meta(site_url, token, wp_post_id, twitter_title=value)


def _read_canonical(site_url: str, token: str, wp_post_id: int) -> wordpress.WordPressResult:
    return wordpress.get_yoast_meta(site_url, token, wp_post_id)


def _write_canonical(site_url: str, token: str, wp_post_id: int, value: str) -> wordpress.WordPressResult:
    return wordpress.set_yoast_meta(site_url, token, wp_post_id, canonical_url=value)


def _read_og_title(site_url: str, token: str, wp_post_id: int) -> wordpress.WordPressResult:
    return wordpress.get_yoast_meta(site_url, token, wp_post_id)


def _write_og_title(site_url: str, token: str, wp_post_id: int, value: str) -> wordpress.WordPressResult:
    return wordpress.set_yoast_meta(site_url, token, wp_post_id, og_title=value)


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
    "twitter": {
        "read": _read_twitter_title,
        "read_key": "twitter_title",  # yoast_get_meta's key for _yoast_wpseo_twitter-title
        "write": _write_twitter_title,
        "tool": "yoast_set_meta",
    },
    "canonical": {
        "read": _read_canonical,
        "read_key": "canonical_url",
        "write": _write_canonical,
        "tool": "yoast_set_meta",
    },
    "opengraph": {
        "read": _read_og_title,
        "read_key": "og_title",  # og_description isn't AI-suggestable as a single value; og_title mirrors the twitter_title precedent
        "write": _write_og_title,
        "tool": "yoast_set_meta",
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
def test_wordpress_connection(project_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Was a real design bug until now: on a project with many pages, this
    used to run the full _resolve_all_pages sweep (0.3s + 1-2 HTTP requests
    PER page) synchronously before ever responding -- a 138-page project
    took 3-6 minutes to say "connected", when a connection test should be a
    few-hundred-millisecond ping. The ping itself now returns immediately;
    page resolution still happens automatically after a passing test (same
    as before), just as a background task the caller doesn't wait on."""
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

    if project_id in _active_resolve_sweeps:
        # Another test-connection click (this or a different browser/device)
        # already has a sweep running for this project -- don't stack a
        # second one on top of it, just let the one in flight finish.
        return {"ok": True, "site": result.data, "resolved_pages": "sweep already in progress, skipped duplicate"}

    _active_resolve_sweeps.add(project_id)
    background_tasks.add_task(_resolve_all_pages_in_background, project_id, conn.site_url, token)
    return {"ok": True, "site": result.data, "resolved_pages": "scheduled in background"}


@router.post("/projects/{project_id}/wordpress/resolve-pages")
def resolve_wordpress_pages(project_id: int, db: Session = Depends(get_db)):
    """Bulk-resolves wp_post_id for every page in this project that doesn't
    have one yet. Manual trigger for the 'Resolve WordPress IDs' button --
    the same work also happens automatically after Test Connection passes,
    but pages crawled after that (or added since) won't have been covered
    yet, so this lets a user force a fresh pass on demand."""
    conn, token = _connected_or_error(db, project_id)
    return _resolve_all_pages(db, project_id, conn.site_url, token)


@router.post("/projects/{project_id}/pages/{page_id}/resolve-wp-post")
def resolve_single_page_wp_post(project_id: int, page_id: int, db: Session = Depends(get_db)):
    """Single-page version for the Fix on Page modal's 'Resolve now' inline
    action -- same underlying resolver, scoped to one page so it's instant
    instead of walking the whole project."""
    page = db.get(models.Page, page_id)
    if not page or page.project_id != project_id:
        raise HTTPException(status_code=404, detail="Page not found")
    conn, token = _connected_or_error(db, project_id)
    summary = _resolve_all_pages(db, project_id, conn.site_url, token, only_page_id=page_id)
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


def _resolve_all_pages(db: Session, project_id: int, site_url: str, token: str, only_page_id: int | None = None) -> dict:
    """Shared by POST /wordpress/resolve-pages (bulk), the single-page
    'Resolve now' modal action (only_page_id), and the background sweep
    scheduled after a connection test passes (see
    _resolve_all_pages_in_background). Politely rate-limited -- see
    _RESOLVE_PAGE_DELAY_SECONDS -- since this can fire one to a few HTTP
    requests per page against the target site.

    Takes site_url as a plain string rather than the WordPressConnection
    row itself so a caller running this in a background task (its own
    freshly-opened db session) never touches an ORM object bound to a
    different, already-closed session."""
    query = db.query(models.Page).filter(models.Page.project_id == project_id, models.Page.wp_post_id.is_(None))
    if only_page_id is not None:
        query = query.filter(models.Page.id == only_page_id)
    pages = query.all()

    resolved = 0
    failures = []
    for i, page in enumerate(pages):
        if i > 0:
            time.sleep(_RESOLVE_PAGE_DELAY_SECONDS)
        result = wordpress.resolve_post_id_by_url(site_url, page.url, token=token)
        if result.ok:
            page.wp_post_id = result.data.get("post_id")
            page.wp_post_type = result.data.get("post_type")
            resolved += 1
        else:
            failures.append({"url": page.url, "reason": result.data.get("reason") or result.error})
    db.commit()
    return {"resolved": resolved, "failed": len(failures), "failures": failures}


def _resolve_all_pages_in_background(project_id: int, site_url: str, token: str) -> None:
    """Runs after the HTTP response for /wordpress/test has already been
    sent -- the request's own `db` session is closed by then (see
    database.get_db's finally block), so this opens a fresh one instead of
    reusing anything request-scoped. Best-effort: resolve_post_id_by_url
    already never raises, and any DB error here just means this sweep's
    results are lost, not that the connection test itself is affected --
    the user already got their ok/error answer before this ever runs."""
    db = SessionLocal()
    try:
        _resolve_all_pages(db, project_id, site_url, token)
    finally:
        db.close()
        _active_resolve_sweeps.discard(project_id)


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
        "verify_status": r.verify_status,
        "verify_checked_at": r.verify_checked_at,
        "verify_detail": r.verify_detail,
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

    # Our own Page copy is NOT updated here any more: it is updated by the
    # verify_deploy job once the public page confirms the value (see
    # deploy_status.apply_verified_value_to_page).

    db.commit()
    db.refresh(revision)

    # Fire-and-forget verification: an independent, external re-fetch of the
    # live page (see app/jobs/handlers/verify_deploy.py) confirming the
    # public site really shows the new value, not just that WordPress
    # accepted the write. Picked up by the light-job worker lane within
    # ~60s -- no delay added here on purpose, since a "mismatch" result
    # right after deploy is itself useful signal (a page cache exists and
    # needs purging), not something to hide by waiting longer.
    db.add(models.Job(
        project_id=suggestion.project_id,
        job_type="verify_deploy",
        payload={"revision_id": revision.id},
    ))
    db.commit()

    return _revision_out(revision)


@router.post("/revisions/{revision_id}/verify")
def reverify_revision(revision_id: int, db: Session = Depends(get_db)):
    """Manual 'Re-check': queues a fresh live-page verification for a deployed
    revision. Useful after a 'saved but not showing' result once a cache has
    been purged, and for revisions deployed before verification existed.
    (Not automatic on purpose: the scheduler stamps scheduled_for with the NEXT
    run time, so delaying a job via that column would push every scheduled job
    back by a full interval.)"""
    revision = db.get(models.SuggestionRevision, revision_id)
    if not revision:
        raise HTTPException(status_code=404, detail="Revision not found")
    if revision.rolled_back_at:
        raise HTTPException(status_code=409, detail="This revision was rolled back; nothing to verify.")

    already_queued = any(
        (j.payload or {}).get("revision_id") == revision.id
        for j in db.query(models.Job).filter(
            models.Job.job_type == "verify_deploy",
            models.Job.status.in_(("queued", "running")),
        )
    )
    if not already_queued:
        db.add(models.Job(project_id=revision.project_id, job_type="verify_deploy", payload={"revision_id": revision.id}))
    revision.verify_status = "pending"
    revision.verify_detail = None
    db.commit()
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
