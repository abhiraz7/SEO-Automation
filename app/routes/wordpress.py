"""
WordPress connection + deploy/rollback routes (Tasks 3.2-3.5).
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models, wordpress
from ..database import get_db

router = APIRouter()


# ── Field deploy registry (Task 3.5) ─────────────────────────────────────
# One entry per deployable field: which Issue.category it applies to, how to
# read the CURRENT value from WordPress (for before_value), and how to WRITE
# the new value. Adding a new field type is one entry here, nothing else.
# meta_description is the only category wired in Task 3.3; title/h1/image_alt
# land in Task 3.5.

def _read_meta_description(site_url: str, token: str, wp_post_id: int) -> wordpress.WordPressResult:
    return wordpress.get_yoast_meta(site_url, token, wp_post_id)


def _write_meta_description(site_url: str, token: str, wp_post_id: int, value: str) -> wordpress.WordPressResult:
    return wordpress.set_yoast_meta(site_url, token, wp_post_id, meta_description=value)


FIELD_DEPLOYERS = {
    "meta_description": {
        "read": _read_meta_description,
        "read_key": "meta_description",  # key inside the read result's .data to extract before_value
        "write": _write_meta_description,
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
    return {"ok": True, "site": result.data}


# ── Deploy / rollback (Tasks 3.3-3.5) ────────────────────────────────────

class DeployIn(BaseModel):
    wp_post_id: int  # No auto-resolution from Page.url -> WP post_id: the
                      # claude-wp-mcp plugin exposes no "find post by URL"
                      # tool, only lookups by numeric ID. Caller supplies it
                      # (documented gap -- see AgentLog).


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

    read_result = deployer["read"](conn.site_url, token, payload.wp_post_id)
    if not read_result.ok and read_result.status == "error":
        raise HTTPException(status_code=502, detail=f"Could not read current value from WordPress: {read_result.error}")
    before_value = read_result.data.get(deployer["read_key"]) if read_result.ok else None

    new_value = suggestion.edited_content or suggestion.content
    write_result = deployer["write"](conn.site_url, token, payload.wp_post_id, new_value)
    if not write_result.ok:
        raise HTTPException(status_code=502, detail=f"Deploy failed: {write_result.error or 'unknown error'}")

    revision = models.SuggestionRevision(
        suggestion_id=suggestion.id,
        project_id=suggestion.project_id,
        field_name=field_name,
        before_value=before_value,
        after_value=new_value,
        wp_post_id=payload.wp_post_id,
        deployed_via=deployer["tool"],
        deploy_result_raw=write_result.data,
    )
    db.add(revision)
    suggestion.status = "deployed"
    suggestion.deployed_at = datetime.now(timezone.utc)
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
