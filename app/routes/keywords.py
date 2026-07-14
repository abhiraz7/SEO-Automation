import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import dataforseo, keyword_provider, models, schemas
from ..database import get_db

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

STOPWORDS = {"the", "a", "an", "for", "to", "of", "in", "on", "and", "is", "how", "what", "near", "me", "vs"}


def _get_project(db: Session, project_id: int) -> models.Project:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _latest_snapshot_view(tracked: models.TrackedKeyword) -> schemas.KeywordWithTrend | None:
    if not tracked.snapshots:
        return None
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


def _overview_data(db: Session, project_id: int) -> dict:
    tracked = db.query(models.TrackedKeyword).filter(models.TrackedKeyword.project_id == project_id).all()
    rows = [r for r in (_latest_snapshot_view(t) for t in tracked) if r is not None]

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


@router.get("/projects/{project_id}/keywords")
def keyword_research_page(project_id: int, request: Request, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    overview = _overview_data(db, project_id)

    # The API schema (KeywordWithTrend) has no id -- the template needs the
    # tracked_keyword id for the "View SERP" link, so pair it up here instead
    # of adding an id field to the shared normalized schema.
    tracked = db.query(models.TrackedKeyword).filter(models.TrackedKeyword.project_id == project_id).all()
    keyword_rows = [
        (t.id, view) for t in tracked if (view := _latest_snapshot_view(t)) is not None
    ]

    return templates.TemplateResponse(
        request,
        "keyword_research.html",
        {"project": project, "overview": overview, "keyword_rows": keyword_rows},
    )


@router.get("/projects/{project_id}/keywords/overview")
def keywords_overview(project_id: int, db: Session = Depends(get_db)):
    _get_project(db, project_id)
    return _overview_data(db, project_id)


@router.post("/projects/{project_id}/keywords/track", response_model=schemas.KeywordWithTrend)
def track_keyword(project_id: int, payload: schemas.TrackKeywordIn, db: Session = Depends(get_db)):
    _get_project(db, project_id)
    keyword = payload.keyword.strip().lower()
    if not keyword:
        raise HTTPException(status_code=400, detail="keyword is required")

    tracked = (
        db.query(models.TrackedKeyword)
        .filter(models.TrackedKeyword.project_id == project_id, models.TrackedKeyword.keyword == keyword)
        .first()
    )
    if not tracked:
        tracked = models.TrackedKeyword(project_id=project_id, keyword=keyword)
        db.add(tracked)
        db.commit()
        db.refresh(tracked)

    normalized = keyword_provider.get_keyword_overview(keyword)
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


@router.delete("/projects/{project_id}/keywords/track/{keyword_id}")
def untrack_keyword(project_id: int, keyword_id: int, db: Session = Depends(get_db)):
    tracked = db.get(models.TrackedKeyword, keyword_id)
    if not tracked or tracked.project_id != project_id:
        raise HTTPException(status_code=404, detail="Tracked keyword not found")
    db.delete(tracked)
    db.commit()
    return {"deleted": keyword_id}


@router.get("/projects/{project_id}/keywords/suggestions", response_model=list[schemas.NormalizedKeyword])
def keyword_suggestions(project_id: int, seed: str, db: Session = Depends(get_db)):
    _get_project(db, project_id)
    return keyword_provider.get_suggestions(seed)


@router.post("/projects/{project_id}/keywords/bulk", response_model=list[schemas.NormalizedKeyword])
def bulk_analysis(project_id: int, payload: schemas.BulkKeywordsIn, db: Session = Depends(get_db)):
    _get_project(db, project_id)
    keywords = [k.strip().lower() for k in payload.keywords if k.strip()]
    if not keywords:
        raise HTTPException(status_code=400, detail="keywords list is empty")
    return keyword_provider.get_keywords_bulk(keywords)


@router.get("/projects/{project_id}/keywords/clusters")
def keyword_clusters(project_id: int, db: Session = Depends(get_db)):
    _get_project(db, project_id)
    tracked = db.query(models.TrackedKeyword).filter(models.TrackedKeyword.project_id == project_id).all()
    saved = db.query(models.SavedKeyword).filter(models.SavedKeyword.project_id == project_id).all()
    keywords = sorted({t.keyword for t in tracked} | {s.keyword for s in saved})
    return _cluster_keywords(keywords)


@router.get("/projects/{project_id}/keywords/saved", response_model=list[schemas.SavedKeywordOut])
def list_saved_keywords(project_id: int, db: Session = Depends(get_db)):
    _get_project(db, project_id)
    return (
        db.query(models.SavedKeyword)
        .filter(models.SavedKeyword.project_id == project_id)
        .order_by(models.SavedKeyword.created_at.desc())
        .all()
    )


@router.post("/projects/{project_id}/keywords/saved", response_model=schemas.SavedKeywordOut)
def save_keyword(project_id: int, payload: schemas.SavedKeywordIn, db: Session = Depends(get_db)):
    _get_project(db, project_id)
    keyword = payload.keyword.strip().lower()
    if not keyword:
        raise HTTPException(status_code=400, detail="keyword is required")

    existing = (
        db.query(models.SavedKeyword)
        .filter(models.SavedKeyword.project_id == project_id, models.SavedKeyword.keyword == keyword)
        .first()
    )
    if existing:
        return existing

    saved = models.SavedKeyword(
        project_id=project_id,
        keyword=keyword,
        volume=payload.volume,
        difficulty=payload.difficulty,
        intent=payload.intent,
    )
    db.add(saved)
    db.commit()
    db.refresh(saved)
    return saved


@router.delete("/projects/{project_id}/keywords/saved/{saved_id}")
def delete_saved_keyword(project_id: int, saved_id: int, db: Session = Depends(get_db)):
    saved = db.get(models.SavedKeyword, saved_id)
    if not saved or saved.project_id != project_id:
        raise HTTPException(status_code=404, detail="Saved keyword not found")
    db.delete(saved)
    db.commit()
    return {"deleted": saved_id}


@router.get("/projects/{project_id}/keywords/{keyword_id}/serp")
def view_serp(project_id: int, keyword_id: int, db: Session = Depends(get_db)):
    tracked = db.get(models.TrackedKeyword, keyword_id)
    if not tracked or tracked.project_id != project_id:
        raise HTTPException(status_code=404, detail="Tracked keyword not found")
    # Live lookup, intentionally not persisted -- SERP results are a point-in-time
    # check, not something we snapshot for trend history (see keyword_provider.py).
    return dataforseo.fetch_serp(tracked.keyword)


@router.get("/projects/{project_id}/keywords/export")
def export_keywords(project_id: int, db: Session = Depends(get_db)):
    _get_project(db, project_id)
    tracked = db.query(models.TrackedKeyword).filter(models.TrackedKeyword.project_id == project_id).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Keyword", "Volume", "Difficulty", "Intent", "Trend"])
    for t in tracked:
        row = _latest_snapshot_view(t)
        if row:
            writer.writerow([row.keyword, row.volume, row.difficulty, row.intent, row.trend])
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=keywords.csv"},
    )
