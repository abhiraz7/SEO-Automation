"""
Keyword Research routes -- standalone tool, workspace-scoped (not project-
scoped). A KeywordWorkspace can exist with no project (pure research mode) or
link to one; /projects/{id}/keywords redirects into the linked workspace so
old links and the project sidebar keep working.
"""
import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import claude, keyword_locations, keyword_provider, keyword_scoring, models, prompt_builder, schemas
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


def _with_score(kw: schemas.NormalizedKeyword, serp_features: dict | None = None):
    """Attach the Worth It verdict to any ok row -- scoring a failed/empty
    lookup would just dress missing data up as a real 0."""
    if kw.status == "ok":
        kw.worth_it = keyword_scoring.score_keyword(kw.volume, kw.difficulty, kw.intent, serp_features)
    return kw


def _parse_trend_points(raw: str | None) -> list[float] | None:
    try:
        points = [float(p) for p in (raw or "").split(",") if p.strip()]
        return points or None
    except ValueError:
        return None


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
    return _with_score(schemas.KeywordWithTrend(
        keyword=tracked.keyword,
        volume=latest.volume,
        difficulty=latest.difficulty,
        intent=latest.intent,
        source=latest.source,
        fetched_at=latest.fetched_at,
        trend=trend,
        trend_confidence=confidence,
        trend_points=_parse_trend_points(latest.trend_points),
    ))


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
    total_volume = sum(r.volume for r in rows if r.volume is not None)
    difficulties = [r.difficulty for r in rows if r.difficulty is not None]
    avg_kd = round(sum(difficulties) / len(difficulties)) if difficulties else None
    # Easy Wins = Worth It band, not SERP position (position needs Rank
    # Tracking, which doesn't exist yet) -- an honest number available today.
    easy_wins = sum(1 for r in rows if r.worth_it and r.worth_it.band == "easy")

    return {
        "tracked_keywords": len(tracked),
        "search_volume": total_volume,
        "avg_kd": avg_kd,
        "easy_wins": easy_wins,
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
    """Live health of both keyword providers (cached 5 min). Not workspace-
    scoped -- provider config is process-wide env state. Lets the UI tell 'you
    forgot to set an API key' AND 'your key/account is broken' apart from 'the
    API has no data' (spec Bug 3 + the unverified-DataForSEO-account case)."""
    return keyword_provider.provider_status()


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

    # The tables are rendered client-side (filters/selection/expand rows need
    # one uniform row pipeline), so ship the rows as JSON with the tracked id
    # bolted on rather than server-rendering the table.
    tracked = db.query(models.TrackedKeyword).filter(models.TrackedKeyword.workspace_id == workspace_id).all()
    keywords_json = jsonable_encoder(
        [{**_latest_snapshot_view(t).model_dump(), "id": t.id} for t in tracked]
    )

    return templates.TemplateResponse(
        request,
        "keyword_research.html",
        {
            "workspace": workspace,
            "overview": overview,
            "keywords_json": keywords_json,
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
            trend_points=",".join(f"{p:g}" for p in normalized.trend_points) if normalized.trend_points else None,
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


@router.get("/keywords/{workspace_id}/suggestions", response_model=dict[str, list[schemas.NormalizedKeyword]])
def keyword_suggestions(
    workspace_id: int, seed: str, location: str = "IN", modes: str = "related,questions",
    db: Session = Depends(get_db),
):
    """modes: comma-separated subset of related,questions,prepositions,comparisons.
    Returns one group per requested mode so the UI can render sections."""
    _get_workspace(db, workspace_id)
    requested = tuple(m for m in (m.strip() for m in modes.split(",")) if m in keyword_provider.SUGGESTION_MODES)
    if not requested:
        raise HTTPException(status_code=400, detail=f"modes must include one of {keyword_provider.SUGGESTION_MODES}")
    groups = keyword_provider.get_suggestion_groups(seed, _require_location(location), requested)
    return {mode: [_with_score(kw) for kw in rows] for mode, rows in groups.items()}


@router.post("/keywords/{workspace_id}/bulk", response_model=list[schemas.NormalizedKeyword])
def bulk_analysis(workspace_id: int, payload: schemas.BulkKeywordsIn, db: Session = Depends(get_db)):
    _get_workspace(db, workspace_id)
    keywords = [k.strip().lower() for k in payload.keywords if k.strip()]
    if not keywords:
        raise HTTPException(status_code=400, detail="keywords list is empty")
    return [_with_score(kw) for kw in keyword_provider.get_keywords_bulk(keywords, _require_location(payload.location))]


@router.get("/keywords/{workspace_id}/detail")
def keyword_detail(workspace_id: int, keyword: str, location: str = "IN", db: Session = Depends(get_db)):
    """Expand-row payload: live metrics + SERP top results + SERP features +
    question keywords, in one call. The SERP-aware Worth It score computed here
    is sharper than the table's (which never pays for a SERP call per row)."""
    _get_workspace(db, workspace_id)
    location = _require_location(location)
    keyword = keyword.strip().lower()
    if not keyword:
        raise HTTPException(status_code=400, detail="keyword is required")

    metrics = keyword_provider.get_keyword_overview(keyword, location)
    serp = keyword_provider.get_serp(keyword, location)
    serp_ok = not serp.get("error")
    features = serp.get("features") if serp_ok else None
    organic = [
        {"rank": i.get("rank_absolute"), "title": i.get("title"), "url": i.get("url")}
        for i in (serp.get("items") or []) if i.get("type") == "organic"
    ][:10] if serp_ok else []

    questions = keyword_provider.get_suggestion_groups(keyword, location, ("questions",)).get("questions", [])

    return {
        "keyword": keyword,
        "location": location,
        "metrics": _with_score(metrics, features),
        "serp_results": organic,
        "serp_features": features,
        "serp_error": serp.get("error"),
        "questions": [q.keyword for q in questions[:8]],
    }


@router.post("/keywords/{workspace_id}/brief")
def generate_brief(workspace_id: int, payload: schemas.TrackKeywordIn, db: Session = Depends(get_db)):
    """Claude-generated client-ready content brief. Uses data we already
    fetch (metrics, SERP, questions) -- Claude adds the judgment layer, at
    roughly a cent per brief on Haiku."""
    _get_workspace(db, workspace_id)
    location = _require_location(payload.location)
    keyword = payload.keyword.strip().lower()
    if not keyword:
        raise HTTPException(status_code=400, detail="keyword is required")

    metrics = keyword_provider.get_keyword_overview(keyword, location)
    serp = keyword_provider.get_serp(keyword, location)
    serp_ok = not serp.get("error")
    features = serp.get("features") or {} if serp_ok else {}
    questions = keyword_provider.get_suggestion_groups(keyword, location, ("questions",)).get("questions", [])

    context = {
        "keyword": keyword,
        "location": keyword_locations.supported_locations().get(location, location),
        "volume": metrics.volume,
        "difficulty": metrics.difficulty,
        "intent": metrics.intent,
        "serp_features": sorted(k for k, v in features.items() if v),
        "serp_results": [
            {"title": i.get("title"), "url": i.get("url")}
            for i in (serp.get("items") or []) if i.get("type") == "organic"
        ][:10],
        "questions": [q.keyword for q in questions[:8]],
    }
    try:
        brief = claude.complete(prompt_builder.build_keyword_brief_prompt(context), max_tokens=1500, temperature=0.7)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Brief generation failed: {e}")
    return {"keyword": keyword, "brief": brief}


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
    # BOM so Excel detects UTF-8 -- without it non-ASCII keywords (Hindi,
    # regional languages) open as mojibake in the tool clients actually use.
    buf.write("﻿")
    writer = csv.writer(buf)
    writer.writerow(["Keyword", "Volume", "Difficulty", "Intent", "Trend"])
    for t in tracked:
        row = _latest_snapshot_view(t)
        writer.writerow([row.keyword, row.volume, row.difficulty, row.intent, row.trend])
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=keywords.csv"},
    )


@router.get("/keywords/{workspace_id}/{keyword_id}/serp")
def view_serp(workspace_id: int, keyword_id: int, location: str = "IN", db: Session = Depends(get_db)):
    tracked = db.get(models.TrackedKeyword, keyword_id)
    if not tracked or tracked.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Tracked keyword not found")
    # Live lookup, intentionally not persisted -- SERP results are a point-in-time
    # check, not something we snapshot for trend history (see keyword_provider.py).
    return keyword_provider.get_serp(tracked.keyword, _require_location(location))
