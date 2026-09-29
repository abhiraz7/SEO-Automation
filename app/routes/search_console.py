"""
Google Search Console connect/callback/property-select routes (Task 4).

/gsc/callback is deliberately flat (no project_id in the path) -- it is the
exact redirect URI registered with Google (GOOGLE_OAUTH_REDIRECT_URI), and
that URI is fixed at Google Cloud Console setup time. project_id instead
travels inside the signed `state` value (google_search_console.sign_state/
verify_state) -- the app has no session/login system to keep it in
server-side across the redirect to Google and back.

Connection model: strictly one GoogleConnection per project (see
prompts/CLAUDE_FEATURE_3_GOOGLE_SEARCH_CONSOLE.md, "Confirmed decisions",
2026-09-29) -- never shared/reused across projects.
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import google_search_console as gsc
from .. import models
from ..database import get_db
from ..services import failure_log

router = APIRouter()


def _connection_out(conn: models.GoogleConnection, db: Session) -> dict:
    properties = (
        db.query(models.SearchConsoleProperty)
        .filter(models.SearchConsoleProperty.connection_id == conn.id)
        .order_by(models.SearchConsoleProperty.site_url)
        .all()
    )
    return {
        "connected": True,
        "google_account_email": conn.google_account_email,
        "status": conn.status,
        "properties": [
            {
                "id": p.id,
                "site_url": p.site_url,
                "permission_level": p.permission_level,
                "selected": p.selected,
            }
            for p in properties
        ],
    }


@router.get("/projects/{project_id}/gsc")
def get_gsc_connection(project_id: int, db: Session = Depends(get_db)):
    if not gsc.is_configured():
        return {"configured": False, "connected": False}
    conn = db.query(models.GoogleConnection).filter(models.GoogleConnection.project_id == project_id).first()
    if not conn:
        return {"configured": True, "connected": False}
    return {"configured": True, **_connection_out(conn, db)}


@router.get("/projects/{project_id}/gsc/connect")
def gsc_connect(project_id: int, db: Session = Depends(get_db)):
    if not gsc.is_configured():
        raise HTTPException(status_code=501, detail="Google Search Console is not configured on this server yet.")
    if not db.get(models.Project, project_id):
        raise HTTPException(status_code=404, detail="Project not found")

    state = gsc.sign_state(project_id)
    auth_url = gsc.build_auth_url(state)
    return RedirectResponse(auth_url)


@router.get("/gsc/callback")
def gsc_callback(code: str | None = None, state: str | None = None, error: str | None = None, db: Session = Depends(get_db)):
    if error:
        # The user declined consent on Google's screen, or Google itself
        # rejected the request -- not a bug in this app, nothing to log as
        # a failure beyond what Google already reported.
        raise HTTPException(status_code=400, detail=f"Google Search Console connection was not completed: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state on callback.")

    project_id = gsc.verify_state(state)
    if project_id is None:
        failure_log.failure("routes.search_console.gsc_callback", "state verification failed")
        raise HTTPException(status_code=400, detail="This connection link is invalid or has expired -- start the connect flow again.")

    if not db.get(models.Project, project_id):
        raise HTTPException(status_code=404, detail="Project not found")

    token_result = gsc.exchange_code_for_tokens(code)
    if not token_result.ok:
        failure_log.failure("routes.search_console.gsc_callback", token_result.error or "token exchange failed", project_id=project_id)
        raise HTTPException(status_code=502, detail=token_result.error or "Could not complete the Google connection.")

    data = token_result.data
    conn = db.query(models.GoogleConnection).filter(models.GoogleConnection.project_id == project_id).first()
    if not conn:
        conn = models.GoogleConnection(project_id=project_id)
        db.add(conn)

    conn.google_account_email = data["google_account_email"]
    conn.access_token_encrypted = gsc.encrypt_token(data["access_token"])
    conn.refresh_token_encrypted = gsc.encrypt_token(data["refresh_token"])
    conn.token_expiry = data["expiry"]
    conn.scope = data["scope"]
    conn.status = "active"
    db.commit()
    db.refresh(conn)

    # Discover properties immediately, so the picker has something to show
    # without a second round trip -- a failure here must not undo the
    # connection that just succeeded (per Ground rules: GSC failures never
    # block or unwind an otherwise-successful step).
    sites_result = gsc.list_sites(data["access_token"], data["refresh_token"], data["expiry"])
    if sites_result.ok:
        _upsert_properties(db, conn, sites_result.data.get("sites", []))
    elif sites_result.status == "error":
        failure_log.failure("routes.search_console.gsc_callback", sites_result.error or "list_sites failed after connect", project_id=project_id)
    # no_data (zero properties) is a valid, silent outcome -- nothing to store.

    return RedirectResponse(f"/projects/{project_id}#gsc")


def _upsert_properties(db: Session, conn: models.GoogleConnection, sites: list[dict]) -> None:
    existing = {
        p.site_url: p
        for p in db.query(models.SearchConsoleProperty).filter(models.SearchConsoleProperty.connection_id == conn.id).all()
    }
    for site in sites:
        site_url = site.get("site_url")
        if not site_url:
            continue
        row = existing.get(site_url)
        if row:
            row.permission_level = site.get("permission_level")
        else:
            db.add(models.SearchConsoleProperty(
                connection_id=conn.id,
                site_url=site_url,
                permission_level=site.get("permission_level"),
            ))
    db.commit()


@router.post("/projects/{project_id}/gsc/properties/{property_id}/select")
def select_gsc_property(project_id: int, property_id: int, db: Session = Depends(get_db)):
    conn = db.query(models.GoogleConnection).filter(models.GoogleConnection.project_id == project_id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="No Google Search Console connection for this project yet")

    target = db.get(models.SearchConsoleProperty, property_id)
    if not target or target.connection_id != conn.id:
        raise HTTPException(status_code=404, detail="Property not found for this connection")

    others = (
        db.query(models.SearchConsoleProperty)
        .filter(models.SearchConsoleProperty.connection_id == conn.id, models.SearchConsoleProperty.id != property_id)
        .all()
    )
    for row in others:
        row.selected = False
    target.selected = True
    db.commit()
    return {"selected_site_url": target.site_url}


@router.post("/projects/{project_id}/gsc/disconnect")
def disconnect_gsc(project_id: int, db: Session = Depends(get_db)):
    conn = db.query(models.GoogleConnection).filter(models.GoogleConnection.project_id == project_id).first()
    if not conn:
        return {"disconnected": True}
    db.delete(conn)  # cascades to search_console_properties via the relationship's cascade="all, delete-orphan"
    db.commit()
    return {"disconnected": True}
