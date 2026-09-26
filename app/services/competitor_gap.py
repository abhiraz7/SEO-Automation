"""
One AI Competitor Gap analysis, end to end:

    target page + keyword + location + device
      -> live SERP (existing provider adapter)          serp_evidence
      -> pick up to 7 comparable competitors             serp_evidence
      -> fetch those pages ONCE (no crawler)             page_evidence
      -> deterministic gaps, all counts computed here    gap_analysis
      -> AI action plan citing evidence ids only         ai_provider / action_plan
      -> (on request) an atomic DRAFT for one action     ai_provider / action_plan

Every stage keeps its OWN outcome (ok / no_data / error) and the run keeps the
first thing that went wrong, so a failed SERP call, a page that could not be
fetched, or an AI answer that could not be used is always visible as exactly that.
It is never rendered as an empty success. A run that dies unexpectedly is left as
an "error" row rather than vanishing.

Nothing here publishes anything. A draft is a proposal a person accepts, edits or
rejects; there is no WordPress call in this module. (The existing deployers only
handle title / meta description / H1 / social / canonical fields, so these
content-level drafts cannot be deployed automatically today.)
"""
import copy
import logging
import threading
from datetime import datetime, timezone
from urllib.parse import urlparse

from .. import ai_provider, models
from ..ai_errors import AIGenerationError
from ..domain_utils import normalize_domain
from ..keyword_locations import supported_locations
from . import action_plan, gap_analysis, page_evidence, serp_evidence

logger = logging.getLogger("competitor_gap")

DEVICES = ("desktop", "mobile")
DRAFTABLE_ACTIONS = ("add", "expand", "rewrite")   # the actions whose result is TEXT
DRAFT_DECISIONS = {"accept": "accepted", "reject": "rejected", "edit": "edited"}
MAX_EVIDENCE_ITEMS = 30
MIN_PAGES_FOR_A_PATTERN = 2
TARGET_EXCERPT_CHARS = 1500


class InputError(ValueError):
    """The request itself is wrong (a 400)."""


class BusyError(RuntimeError):
    """An analysis is already running for this project (a 409)."""


_running: set[int] = set()
_running_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── input ─────────────────────────────────────────────────────────────────

def validate_inputs(project: models.Project, target_url: str, keyword: str, location: str, device: str) -> dict:
    url = (target_url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or len(url) > 2048:
        raise InputError("Enter the full page URL, starting with http:// or https://")
    site = normalize_domain(project.base_url)
    host = normalize_domain(url)
    if not site or not (host == site or host.endswith("." + site)):
        raise InputError(f"The target page must be on this project's website ({site or 'no base URL set'}).")

    kw = " ".join((keyword or "").split())
    if not 2 <= len(kw) <= 200:
        raise InputError("Enter a target keyword (2 to 200 characters).")

    loc = (location or "").strip().upper()
    if loc not in supported_locations():
        raise InputError(f"Unsupported location {location!r}. Choose one of: {', '.join(supported_locations())}.")

    dev = (device or "").strip().lower()
    if dev not in DEVICES:
        raise InputError("Device must be desktop or mobile.")
    return {"target_url": url, "keyword": kw, "location": loc, "device": dev}


# ── the target page ───────────────────────────────────────────────────────

def _find_page(db, project: models.Project, target_url: str) -> models.Page | None:
    base = target_url.rstrip("/")
    return (
        db.query(models.Page)
        .filter(models.Page.project_id == project.id, models.Page.url.in_({target_url, base, base + "/"}))
        .first()
    )


def _target_from_page(page: models.Page) -> dict | None:
    """The project's own crawled data, when it has enough to compare with; None when
    it does not (then the page is fetched live instead)."""
    headings = [
        {"tag": h["tag"], "text": h["text"]}
        for h in (page.heading_structure or []) if isinstance(h, dict) and h.get("tag") in ("h2", "h3") and h.get("text")
    ]
    if not headings:
        headings = [{"tag": "h2", "text": t} for t in (page.h2 or []) if t]
    text = (page.fit_markdown or page.custom_content or "").strip()
    if not headings and not text:
        return None
    h1s = page.h1 if isinstance(page.h1, list) else ([page.h1] if page.h1 else [])
    return {
        "url": page.url, "title": page.title, "h1": h1s[0] if h1s else None, "headings": headings,
        "text": text[:page_evidence.MAX_TEXT_CHARS], "questions": page_evidence.questions_in(text),
        "word_count": page.word_count or len(text.split()), "source": "the project's crawled page data",
    }


def _target_from_live(url: str, fetched: dict) -> dict:
    return {
        "url": url, "title": fetched["title"], "h1": fetched["h1"], "headings": fetched["headings"], "text": fetched["text"],
        "questions": fetched["questions"], "word_count": fetched["word_count"],
        "source": f"a live fetch of the page ({fetched['fetch_method']})",
    }


# ── run ───────────────────────────────────────────────────────────────────

def _finish(db, run: models.CompetitorAnalysisRun, status: str, error: str | None = None) -> models.CompetitorAnalysisRun:
    run.status = status
    run.error = error
    db.commit()
    return run


def run_analysis(db, project: models.Project, target_url: str, keyword: str, location: str, device: str) -> models.CompetitorAnalysisRun:
    """Runs one analysis and returns the persisted run. Raises InputError for a bad
    request and BusyError if this project already has one running; everything else
    (a provider failure, a page that will not load, an unusable AI answer) ends up
    recorded ON the run."""
    inputs = validate_inputs(project, target_url, keyword, location, device)
    with _running_lock:
        if project.id in _running:
            raise BusyError("An analysis is already running for this project. Wait for it to finish.")
        _running.add(project.id)
    try:
        page = _find_page(db, project, inputs["target_url"])
        run = models.CompetitorAnalysisRun(
            project_id=project.id, page_id=page.id if page else None, target_url=inputs["target_url"],
            keyword=inputs["keyword"], location=inputs["location"], device=inputs["device"],
            status="error", error="The analysis did not finish (it was interrupted).", plan_status="not_run",
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id
        try:
            _execute(db, run, project, page)
        except Exception as exc:  # noqa: BLE001 -- last resort: per-stage failures are handled inside _execute
            logger.exception("competitor gap analysis %s crashed", run_id)
            db.rollback()
            run = db.get(models.CompetitorAnalysisRun, run_id)
            _finish(db, run, "error", f"Unexpected error: {exc}")
        db.refresh(run)
        return run
    finally:
        with _running_lock:
            _running.discard(project.id)


def _execute(db, run: models.CompetitorAnalysisRun, project: models.Project, page: models.Page | None) -> None:
    # 1. the target page --------------------------------------------------------
    target = _target_from_page(page) if page else None
    if target is None:
        fetched = page_evidence.fetch_page_evidence(run.target_url)
        if fetched["status"] != "ok":
            _finish(db, run, "error", f"The target page could not be read ({fetched['status']}): {fetched['error']}")
            return
        target = _target_from_live(run.target_url, fetched)
    run.target_snapshot = {
        "url": target["url"], "title": target["title"], "h1": target["h1"], "headings": target["headings"],
        "word_count": target["word_count"], "excerpt": target["text"][:TARGET_EXCERPT_CHARS], "source": target["source"],
    }
    db.commit()

    # 2. the live SERP ----------------------------------------------------------
    serp = serp_evidence.fetch_serp_evidence(run.keyword, run.location, run.device)
    run.source = serp["source"]
    features = serp.get("features") or {}
    run.serp_features = sorted(k for k, v in features.items() if v and k != "ads") + (["ads"] if features.get("ads") else [])
    if serp["status"] == "error":
        _finish(db, run, "error", f"The search results could not be fetched: {serp['error']}")
        return
    run.serp_total_results = len(serp["organic"])
    if serp["status"] == "no_data":
        _finish(db, run, "no_data", serp["error"])
        return

    # 3. comparable competitors -------------------------------------------------
    selected, excluded = serp_evidence.select_competitors(serp["organic"], run.target_url)
    reasons = {e["url"]: e["excluded_reason"] for e in excluded}
    selected_urls = {r["url"] for r in selected}
    run.competitors_selected = len(selected)
    snapshots: dict[str, models.CompetitorPageSnapshot] = {}
    for r in serp["organic"]:
        chosen = r["url"] in selected_urls
        snap = models.CompetitorPageSnapshot(
            analysis_run_id=run.id, url=r["url"], position=r["position"], result_class=r["result_class"], selected=chosen,
            title=r["title"], fetch_status="pending" if chosen else "skipped", error=None if chosen else reasons.get(r["url"]),
        )
        db.add(snap)
        snapshots[r["url"]] = snap
    db.commit()
    if not selected:
        _finish(db, run, "no_data", "None of the top results are comparable pages: they are forums, marketplaces, social pages, "
                                    "home pages, category pages, or your own site.")
        return

    # 4. fetch them once --------------------------------------------------------
    fetched_pages = page_evidence.fetch_pages_evidence([r["url"] for r in selected])
    usable: list[dict] = []
    for r in selected:
        f = fetched_pages[r["url"]]
        snap = snapshots[r["url"]]
        snap.title = f["title"] or snap.title
        snap.h1, snap.headings_json, snap.text = f["h1"], f["headings"], f["text"]
        snap.word_count, snap.fetch_method = f["word_count"], f["fetch_method"]
        snap.fetch_status, snap.extraction_confidence, snap.error = f["status"], f["extraction_confidence"], f["error"]
        if f["status"] == "ok":
            usable.append({
                "position": r["position"], "domain": r["domain"], "url": r["url"], "title": f["title"], "h1": f["h1"],
                "headings": f["headings"], "text": f["text"], "questions": f["questions"], "word_count": f["word_count"],
            })
    run.competitors_analyzed = len(usable)
    db.commit()
    if len(usable) < MIN_PAGES_FOR_A_PATTERN:
        _finish(db, run, "no_data",
                f"Only {len(usable)} of {len(selected)} comparable pages could be analysed; at least "
                f"{MIN_PAGES_FOR_A_PATTERN} are needed to see a pattern. See the page table for why each failed.")
        return

    # 5. deterministic gaps -----------------------------------------------------
    analysis = gap_analysis.analyse(target, usable, serp, run.keyword)
    run.intent, run.format_distribution = analysis["intent"], analysis["format_distribution"]
    rows = []
    for g in analysis["gaps"]:
        row = models.CompetitorGap(
            analysis_run_id=run.id, gap_type=g["gap_type"], label=g["label"][:500], target_coverage=g["target_coverage"],
            competitor_count=g["competitor_count"], competitor_total=g["competitor_total"], evidence_json=g["evidence"],
            confidence=g["confidence"], recommended_action=g["recommended_action"],
        )
        db.add(row)
        rows.append(row)
    run.status = "ok" if len(usable) == len(selected) else "partial"
    run.error = None
    db.commit()
    for row in rows:
        db.refresh(row)

    # 6. the AI plan (its failure never erases the gaps above) ------------------
    _plan_stage(db, run, project, target, serp, rows, analysis)


def _gap_dict(row: models.CompetitorGap) -> dict:
    return {
        "id": row.id, "gap_type": row.gap_type, "label": row.label, "target_coverage": row.target_coverage,
        "competitor_count": row.competitor_count, "competitor_total": row.competitor_total, "confidence": row.confidence,
    }


def _bundle(run: models.CompetitorAnalysisRun, target_summary: dict, serp_results: list[dict], evidence: list[dict],
            question_data_available: bool) -> dict:
    return {
        "keyword": run.keyword, "location": run.location, "device": run.device,
        "target": target_summary,
        "serp": {
            "analyzed": run.competitors_analyzed, "selected": run.competitors_selected, "total_results": run.serp_total_results,
            "features": run.serp_features or [], "results": serp_results, "question_data_available": question_data_available,
        },
        "intent": run.intent, "format_distribution": run.format_distribution, "evidence": evidence,
    }


def _plan_stage(db, run, project, target, serp, rows, analysis) -> None:
    actionable = [r for r in rows if r.target_coverage != "covered"]
    if not actionable:
        run.plan_status = "no_data"
        run.plan_error = "No gaps found: the page already covers what the comparable ranking pages recurringly cover."
        db.commit()
        return
    evidence = action_plan.build_evidence_list([_gap_dict(r) for r in rows][:MAX_EVIDENCE_ITEMS])
    bundle = _bundle(
        run,
        {"url": target["url"], "title": target["title"], "h1": target["h1"], "headings": [h["text"] for h in target["headings"]],
         "word_count": target["word_count"]},
        [{"position": r["position"], "domain": r["domain"], "result_class": r["result_class"], "title": r["title"]} for r in serp["organic"]],
        evidence, analysis["question_data_available"],
    )
    profile = db.query(models.BusinessProfile).filter(models.BusinessProfile.project_id == project.id).first()
    try:
        plan = ai_provider.generate_action_plan(db, bundle, profile)
    except AIGenerationError as exc:
        logger.warning("action plan for run %s failed: %s", run.id, exc)
        run.plan_status, run.plan_error = "error", str(exc)
        db.commit()
        return
    run.action_plan_json = {"actions": plan["actions"], "rejected": plan["rejected"], "warnings": plan["warnings"], "generated_at": _now()}
    run.plan_status = "ok" if plan["actions"] else "no_data"
    run.plan_error = None if plan["actions"] else "The AI's suggestions were all rejected because they were not backed by the supplied evidence."
    by_id = {r.id: r for r in rows}
    for action in plan["actions"]:
        for ev in action["evidence"]:
            row = by_id.get(ev.get("gap_id"))
            if row is not None and row.target_coverage != "covered":
                row.recommended_action = action["type"]
    db.commit()


# ── read model ────────────────────────────────────────────────────────────

def summary_line(run: models.CompetitorAnalysisRun) -> str:
    sel, ok = run.competitors_selected or 0, run.competitors_analyzed or 0
    if not sel:
        return "No comparable competitors were selected."
    return f"{ok} of {sel} comparable competitors successfully analysed"


def notices(run: models.CompetitorAnalysisRun) -> list[str]:
    out = []
    if run.source == "semrush":
        out.append("The search provider fell back to Semrush, which returns only domains and URLs: no result titles and no "
                   "People-Also-Ask questions. Question evidence is unavailable for this run, and the results are for desktop.")
    if run.status == "partial":
        out.append(f"Only {run.competitors_analyzed} of {run.competitors_selected} comparable pages could be analysed. "
                   "The rest failed (see the page table); patterns are based on the pages that worked.")
    if (run.competitors_analyzed or 0) and run.competitors_analyzed < 4:
        out.append("Small sample: patterns drawn from fewer than 4 pages are low confidence.")
    return out


def run_detail(db, run: models.CompetitorAnalysisRun) -> dict:
    snaps = (db.query(models.CompetitorPageSnapshot).filter(models.CompetitorPageSnapshot.analysis_run_id == run.id)
             .order_by(models.CompetitorPageSnapshot.position).all())
    gaps = db.query(models.CompetitorGap).filter(models.CompetitorGap.analysis_run_id == run.id).order_by(models.CompetitorGap.id).all()
    by_gap = {g.id: g for g in gaps}
    plan = run.action_plan_json or {}
    actions = []
    for a in plan.get("actions") or []:
        gap_ids = [e["gap_id"] for e in a.get("evidence") or [] if e.get("gap_id") in by_gap]
        actions.append({**a, "gap_ids": gap_ids, "draftable": a["type"] in DRAFTABLE_ACTIONS})
    return {
        "id": run.id, "project_id": run.project_id, "target_url": run.target_url, "keyword": run.keyword,
        "location": run.location, "device": run.device, "status": run.status, "error": run.error, "source": run.source,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "intent": run.intent, "format_distribution": run.format_distribution, "serp_features": run.serp_features or [],
        "serp_total_results": run.serp_total_results, "competitors_selected": run.competitors_selected,
        "competitors_analyzed": run.competitors_analyzed, "summary_line": summary_line(run), "notices": notices(run),
        "snapshots": [{
            "position": s.position, "url": s.url, "domain": normalize_domain(s.url), "result_class": s.result_class,
            "selected": bool(s.selected), "title": s.title, "h1": s.h1, "word_count": s.word_count, "fetch_method": s.fetch_method,
            "fetch_status": s.fetch_status, "extraction_confidence": s.extraction_confidence, "error": s.error,
        } for s in snaps],
        "gaps": [{
            "id": g.id, "gap_type": g.gap_type, "label": g.label, "target_coverage": g.target_coverage,
            "competitor_count": g.competitor_count, "competitor_total": g.competitor_total, "confidence": g.confidence,
            "recommended_action": g.recommended_action, "evidence": g.evidence_json or {},
        } for g in gaps],
        "plan": {"status": run.plan_status, "error": run.plan_error, "actions": actions,
                 "rejected": plan.get("rejected") or [], "warnings": plan.get("warnings") or []},
    }


# ── drafts ────────────────────────────────────────────────────────────────

def _find_action(run: models.CompetitorAnalysisRun, action_id: str) -> dict:
    for a in (run.action_plan_json or {}).get("actions") or []:
        if a["id"] == action_id:
            return a
    raise LookupError(f"Action {action_id!r} not found in this analysis.")


def _save_action(db, run: models.CompetitorAnalysisRun, action_id: str, **changes) -> dict:
    """Replaces the plan JSON with a modified COPY (assigning a new object is what
    makes SQLAlchemy notice a JSON column changed)."""
    plan = copy.deepcopy(run.action_plan_json or {})
    for a in plan.get("actions") or []:
        if a["id"] == action_id:
            a.update(changes)
            run.action_plan_json = plan
            db.commit()
            return a
    raise LookupError(f"Action {action_id!r} not found in this analysis.")


def generate_draft(db, run: models.CompetitorAnalysisRun, action_id: str) -> dict:
    """The AI's atomic draft for one action, validated, saved next to the action with
    status 'pending'. Raises LookupError (no such action), InputError (this action
    produces no text, or already has a decided draft) or AIGenerationError."""
    action = _find_action(run, action_id)
    if action["type"] not in DRAFTABLE_ACTIONS:
        raise InputError(f"'{action['type']}' actions do not produce draft text; only {', '.join(DRAFTABLE_ACTIONS)} do.")
    existing = action.get("draft") or {}
    if existing.get("status") in ("accepted", "edited"):
        raise InputError("This action already has a draft you accepted or edited. It is kept, not regenerated.")

    snaps = (db.query(models.CompetitorPageSnapshot)
             .filter(models.CompetitorPageSnapshot.analysis_run_id == run.id, models.CompetitorPageSnapshot.fetch_status == "ok").all())
    competitor_texts = [s.text for s in snaps if s.text]
    ts = run.target_snapshot or {}
    evidence = action_plan.build_evidence_list([])  # the draft prompt uses the action's own expanded evidence
    bundle = {
        "keyword": run.keyword, "location": run.location, "device": run.device, "evidence": evidence,
        "target": {"url": ts.get("url") or run.target_url, "title": ts.get("title"), "h1": ts.get("h1"),
                   "headings": [h["text"] for h in ts.get("headings") or []], "word_count": ts.get("word_count"),
                   "excerpt": ts.get("excerpt")},
    }
    profile = db.query(models.BusinessProfile).filter(models.BusinessProfile.project_id == run.project_id).first()
    result = ai_provider.generate_gap_draft(db, bundle, action, competitor_texts, profile)
    draft = {"text": result["draft"], "status": "pending", "claims_to_verify": result["claims_to_verify"],
             "warnings": result["warnings"], "generated_at": _now()}
    _save_action(db, run, action_id, draft=draft)
    return draft


def decide_draft(db, run: models.CompetitorAnalysisRun, action_id: str, decision: str, text: str | None = None) -> dict:
    """accept / reject / edit a draft. Only records the decision: nothing is
    published or deployed."""
    if decision not in DRAFT_DECISIONS:
        raise InputError("Decision must be accept, reject or edit.")
    action = _find_action(run, action_id)
    draft = dict(action.get("draft") or {})
    if not draft.get("text"):
        raise InputError("This action has no draft yet. Generate one first.")
    if decision == "edit":
        edited = (text or "").strip()
        if not edited:
            raise InputError("The edited draft cannot be empty.")
        if len(edited) > action_plan.MAX_DRAFT_CHARS:
            raise InputError(f"The edited draft is longer than {action_plan.MAX_DRAFT_CHARS} characters.")
        draft["edited_text"] = edited
    draft["status"] = DRAFT_DECISIONS[decision]
    draft["decided_at"] = _now()
    _save_action(db, run, action_id, draft=draft)
    return draft
