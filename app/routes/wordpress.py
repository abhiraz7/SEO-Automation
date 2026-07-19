"""
WordPress connection routes (Task 3.2). Stores/tests a project's
claude-wp-mcp plugin connection; the actual deploy/rollback routes live in
this same module once Tasks 3.3-3.5 land.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models, wordpress
from ..database import get_db

router = APIRouter()


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
