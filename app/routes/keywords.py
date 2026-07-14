"""
Keyword Research routes -- standalone tool, workspace-scoped (not project-
scoped). A KeywordWorkspace can exist with no project (pure research mode) or
link to one; /projects/{id}/keywords redirects into the linked workspace so
old links and the project sidebar keep working.
"""
import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import dataforseo, keyword_locations, keyword_provider, models, schemas, semrush
from ..database import get_db

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

STOPWORDS = {"the", "a", "an", "for", "to", "of", "in", "on", "and", "is", "how", "what", "near", "me", "vs"}


def _require_location(location: str) -> str:
    """Reject unsupported ISO codes with a 400 instead of letting the adapters
    each fail separately -- and never silently fall back to another market."""
    if not keyword_locations.is_supported(location):
        raise HTTPException(status_code=400, detail=f"Unsupported location: {location}")
    return location.upper()


def _get_workspace(db: Session, workspace_id: int) -> models.KeywordWorkspace:
    workspace = db.get(models.KeywordWorkspace, workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return workspace


def _latest_snapshot_view(tracked: models.TrackedKeyword) -> schemas.KeywordWithTrend:
    """A tracked keyword with no snapshots is one whose every lookup so far
    came back no_data/error (failed lookups don't write snapshots) -- shown as
    an explicit 'no data' row rather than hidden or faked as zeros."""
    if not tracked.snapshots:
        return schemas.KeywordWithTrend(
            keyword=tracked.keyword,
            source="none",
            fetched_at=tracked.created_at,
            status="no_data",
            trend="stable",
            trend_confidence="insufficient_data",
        )
    latest = max(tracked.snapshots, key=lambda s: s.fetched_at)
    trend, confidence = keyword_provider.compute_trend(tracked.snapshots)
    return schemas.KeywordWithTrend(
        keyword=tracked.keyword,
        volume=latest.volume,
        difficulty=latest.difficulty,
        intent=latest.intent,
        source=latest.source,
        fetched_at=latest.fetched_at,
        trend=trend,
        trend_confidence=confidence,
    )


def _cluster_keywords(keywords: list[str]) -> list[dict]:
    """
    Simple root-term clustering for MVP: group keywords by their longest
    non-stopword token. No ML/embedding clustering -- out of scope per plan.
    """
    clusters: dict[str, list[str]] = {}
    for kw in keywords:
        tokens = [t for t in kw.lower().split() if t not in STOPWORDS] or kw.lower().split()
        root = max(tokens, key=len)
        clusters.setdefault(root, []).append(kw)
    return [{"root": root, "keywords": kws} for root, kws in clusters.items()]


def _overview_data(db: Session, workspace_id: int) -> dict:
    tracked = db.query(models.TrackedKeyword).filter(models.TrackedKeyword.workspace_id == workspace_id).all()
    rows = [_latest_snapshot_view(t) for t in tracked]

    # "Avg. Position" and "Easy Wins" (position 11-20) in the mockup are SERP
    # rank stats. position on KeywordSnapshot is only ever populated once Rank
    # Tracking (same tracked_keywords/keyword_snapshots tables) starts writing
    # real ranks -- until then there is nothing honest to show here, so both
    # stay None and data_quality tells the frontend to render "--"/"Coming
    # soon" instead of a 0 or a proxy number. No code change needed here once
    # Rank Tracking ships: real position values just start appearing.
    latest_positions = [
        s.position
        for t in tracked
        if t.snapshots
        for s in [max(t.snapshots, key=lambda s: s.fetched_at)]
        if s.position is not None
    ]
    data_quality = "live" if latest_positions else "position_data_pending"
    avg_position = round(sum(latest_positions) / len(latest_positions), 1) if latest_positions else None
    easy_wins = sum(1 for p in latest_positions if 11 <= p <= 20) if latest_positions else None

    total_volume = sum(r.volume for r in rows if r.volume is not None)

    return {
        "tracked_keywords": len(tracked),
        "avg_position": avg_position,
        "search_volume": total_volume,
        "easy_wins": easy_wins,
        "data_quality": data_quality,
        "keywords": rows,
    }


# --- Workspace management -------------------------------------------------

@router.get("/keywords")
def keywords_home(request: Request, db: Session = Depends(get_db)):
    """Workspace picker. One workspace -> straight into it; otherwise a list
    with a create form (also the landing page when no workspace exists yet)."""
    workspaces = db.query(models.KeywordWorkspace).order_by(models.KeywordWorkspace.created_at).all()
    if len(workspaces) == 1:
        return RedirectResponse(f"/keywords/{workspaces[0].id}", status_code=303)
    projects = db.query(models.Project).order_by(models.Project.name).all()
    return templates.TemplateResponse(
        request,
        "keyword_workspaces.html",
        {
            "workspaces": workspaces,
            "projects": projects,
            "locations": keyword_locations.supported_locations(),
            "default_location": keyword_locations.DEFAULT_LOCATION,
        },
    )


@router.post("/keywords/workspaces", response_model=schemas.WorkspaceOut)
def create_workspace(payload: schemas.WorkspaceIn, db: Session = Depends(get_db)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    location = _require_location(payload.default_location)
    if payload.project_id is not None and not db.get(models.Project, payload.project_id):
        raise HTTPException(status_code=404, detail="Linked project not found")

    workspace = models.KeywordWorkspace(
        name=name, default_location=location, project_id=payload.project_id
    )
    db.add(workspace)
    db.commit()
    db.refresh(workspace)
    return workspace


@router.get("/keywords/provider-status")
def provider_status():
    """Which keyword providers have credentials configured. Not workspace-
    scoped -- provider config is process-wide env state. Lets the UI tell 'you
    forgot to set an API key' apart from 'the API has no data' (spec Bug 3)."""
    semrush_ok = semrush.is_configured()
    dataforseo_ok = dataforseo.is_configured()
    return {
        "semrush": semrush_ok,
        "dataforseo": dataforseo_ok,
        "any_configured": semrush_ok or dataforseo_ok,
    }


@router.get("/projects/{project_id}/keywords")
def project_keywords_redirect(project_id: int, db: Session = Depends(get_db)):
    """Old project-scoped URL (and the project sidebar link). Redirects into
    the project's linked workspace, creating one on first use."""
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    workspace = (
        db.query(models.KeywordWorkspace)
        .filter(models.KeywordWorkspace.project_id == project_id)
        .first()
    )
    if not workspace:
        workspace = models.KeywordWorkspace(name=project.name, project_id=project_id)
        db.add(workspace)
        db.commit()
        db.refresh(workspace)
    return RedirectResponse(f"/keywords/{workspace.id}", status_code=303)


# --- Main tool ------------------------------------------------------------

@router.get("/keywords/{workspace_id}")
def keyword_research_page(workspace_id: int, request: Request, db: Session = Depends(get_db)):
    workspace = _get_workspace(db, workspace_id)
    overview = _overview_data(db, workspace_id)

    # The API schema (KeywordWithTrend) has no id -- the template needs the
    # tracked_keyword id for the "View SERP" link, so pair it up here instead
    # of adding an id field to the shared normalized schema.
    tracked = db.query(models.TrackedKeyword).filter(models.TrackedKeyword.workspace_id == workspace_id).all()
    keyword_rows = [(t.id, _latest_snapshot_view(t)) for t in tracked]

    return templates.TemplateResponse(
        request,
        "keyword_research.html",
        {
            "workspace": workspace,
            "overview": overview,
            "keyword_rows": keyword_rows,
            "locations": keyword_locations.supported_locations(),
            "default_location": workspace.default_location or keyword_locations.DEFAULT_LOCATION,
        },
    )


@router.get("/keywords/{workspace_id}/overview")
def keywords_overview(workspace_id: int, db: Session = Depends(get_db)):
    _get_workspace(db, workspace_id)
    return _overview_data(db, workspace_id)


@router.post("/keywords/{workspace_id}/track", response_model=schemas.KeywordWithTrend)
def track_keyword(workspace_id: int, payload: schemas.TrackKeywordIn, db: Session = Depends(get_db)):
    _get_workspace(db, workspace_id)
    keyword = payload.keyword.strip().lower()
    if not keyword:
        raise HTTPException(status_code=400, detail="keyword is required")
    location = _require_location(payload.location)

    tracked = (
        db.query(models.TrackedKeyword)
        .filter(models.TrackedKeyword.workspace_id == workspace_id, models.TrackedKeyword.keyword == keyword)
        .first()
    )
    newly_created = tracked is None
    if newly_created:
        tracked = models.TrackedKeyword(workspace_id=workspace_id, keyword=keyword)
        db.add(tracked)
        db.commit()
        db.refresh(tracked)

    normalized = keyword_provider.get_keyword_overview(keyword, location)

    if normalized.status == "error":
        # Don't leave a just-created row behind for a lookup that failed
        # outright -- the user should fix the cause (keys/network) and retry.
        if newly_created:
            db.delete(tracked)
            db.commit()
        raise HTTPException(status_code=502, detail=f"Keyword lookup failed: {normalized.error}")

    # no_data keeps the tracked keyword but writes no snapshot -- a fabricated
    # zero-value snapshot would poison compute_trend() forever (spec Bug 1).
    if normalized.status == "ok":
        db.add(models.KeywordSnapshot(
            tracked_keyword_id=tracked.id,
            volume=normalized.volume,
            difficulty=normalized.difficulty,
            intent=normalized.intent,
            source=normalized.source,
            fetched_at=normalized.fetched_at,
        ))
        db.commit()
        db.refresh(tracked)

    return _latest_snapshot_view(tracked)


@router.delete("/keywords/{workspace_id}/track/{keyword_id}")
def untrack_keyword(workspace_id: int, keyword_id: int, db: Session = Depends(get_db)):
    tracked = db.get(models.TrackedKeyword, keyword_id)
    if not tracked or tracked.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Tracked keyword not found")
    db.delete(tracked)
    db.commit()
    return {"deleted": keyword_id}


@router.get("/keywords/{workspace_id}/suggestions", response_model=list[schemas.NormalizedKeyword])
def keyword_suggestions(workspace_id: int, seed: str, location: str = "IN", db: Session = Depends(get_db)):
    _get_workspace(db, workspace_id)
    return keyword_provider.get_suggestions(seed, _require_location(location))


@router.post("/keywords/{workspace_id}/bulk", response_model=list[schemas.NormalizedKeyword])
def bulk_analysis(workspace_id: int, payload: schemas.BulkKeywordsIn, db: Session = Depends(get_db)):
    _get_workspace(db, workspace_id)
    keywords = [k.strip().lower() for k in payload.keywords if k.strip()]
    if not keywords:
        raise HTTPException(status_code=400, detail="keywords list is empty")
    return keyword_provider.get_keywords_bulk(keywords, _require_location(payload.location))


@router.get("/keywords/{workspace_id}/clusters")
def keyword_clusters(workspace_id: int, db: Session = Depends(get_db)):
    _get_workspace(db, workspace_id)
    tracked = db.query(models.TrackedKeyword).filter(models.TrackedKeyword.workspace_id == workspace_id).all()
    saved = db.query(models.SavedKeyword).filter(models.SavedKeyword.workspace_id == workspace_id).all()
    keywords = sorted({t.keyword for t in tracked} | {s.keyword for s in saved})
    return _cluster_keywords(keywords)


@router.get("/keywords/{workspace_id}/saved", response_model=list[schemas.SavedKeywordOut])
def list_saved_keywords(workspace_id: int, db: Session = Depends(get_db)):
    _get_workspace(db, workspace_id)
    return (
        db.query(models.SavedKeyword)
        .filter(models.SavedKeyword.workspace_id == workspace_id)
        .order_by(models.SavedKeyword.created_at.desc())
        .all()
    )


@router.post("/keywords/{workspace_id}/saved", response_model=schemas.SavedKeywordOut)
def save_keyword(workspace_id: int, payload: schemas.SavedKeywordIn, db: Session = Depends(get_db)):
    _get_workspace(db, workspace_id)
    keyword = payload.keyword.strip().lower()
    if not keyword:
        raise HTTPException(status_code=400, detail="keyword is required")

    existing = (
        db.query(models.SavedKeyword)
        .filter(models.SavedKeyword.workspace_id == workspace_id, models.SavedKeyword.keyword == keyword)
        .first()
    )
    if existing:
        return existing

    saved = models.SavedKeyword(
        workspace_id=workspace_id,
        keyword=keyword,
        volume=payload.volume,
        difficulty=payload.difficulty,
        intent=payload.intent,
    )
    db.add(saved)
    db.commit()
    db.refresh(saved)
    return saved


@router.delete("/keywords/{workspace_id}/saved/{saved_id}")
def delete_saved_keyword(workspace_id: int, saved_id: int, db: Session = Depends(get_db)):
    saved = db.get(models.SavedKeyword, saved_id)
    if not saved or saved.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Saved keyword not found")
    db.delete(saved)
    db.commit()
    return {"deleted": saved_id}


@router.get("/keywords/{workspace_id}/export")
def export_keywords(workspace_id: int, db: Session = Depends(get_db)):
    _get_workspace(db, workspace_id)
    tracked = db.query(models.TrackedKeyword).filter(models.TrackedKeyword.workspace_id == workspace_id).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Keyword", "Volume", "Difficulty", "Intent", "Trend"])
    for t in tracked:
        row = _latest_snapshot_view(t)
        writer.writerow([row.keyword, row.volume, row.difficulty, row.intent, row.trend])
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=keywords.csv"},
    )


@router.get("/keywords/{workspace_id}/{keyword_id}/serp")
def view_serp(workspace_id: int, keyword_id: int, location: str = "IN", db: Session = Depends(get_db)):
    tracked = db.get(models.TrackedKeyword, keyword_id)
    if not tracked or tracked.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Tracked keyword not found")
    # Live lookup, intentionally not persisted -- SERP results are a point-in-time
    # check, not something we snapshot for trend history (see keyword_provider.py).
    return dataforseo.fetch_serp(tracked.keyword, _require_location(location))
