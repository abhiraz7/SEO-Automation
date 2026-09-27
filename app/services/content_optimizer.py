"""
The AI Content Optimizer, end to end:

    existing page + keyword
      -> SERP / competitor evidence (a stored analysis is reused when fresh)   competitor_gap
      -> the page as it is: sections, title, meta, H1, related pages          optimizer_page
      -> numbered evidence + what this page can support                       optimizer_plan
      -> ONE model call: <= 5 atomic suggestions citing evidence ids          ai_provider
      -> resolved against the application's own data                          optimizer_plan
      -> deterministic validation of every suggestion                         optimizer_validation
      -> stored as ordinary Suggestions, reviewed with the EXISTING accept / edit / reject /
         deploy / rollback workflow

This module reuses the existing Suggestion state machine instead of building a second
one. An optimizer suggestion is a Suggestion (pending -> accepted / edited / rejected ->
deployed) under an Issue whose rule starts with models.OPTIMIZER_RULE_PREFIX; deploy,
rollback, revisions and live verification are the existing ones. Only title, meta
description and H1 suggestions can be deployed today (that is all the WordPress
connector can write); the rest are reviewed and approved here and applied by a person.

Every stage keeps its own outcome and the run says which: ok / no_change / no_data /
error. A provider failure is never shown as an empty result, "no change" is a real
answer the model or the evidence may give, and a suggestion that fails validation is
stored and shown, blocked, with its reasons.
"""
import hashlib
import logging
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError

from .. import ai_provider, deploy_status, models
from ..ai_errors import AIGenerationError
from ..domain_utils import normalize_domain
from . import competitor_gap, optimizer_page, optimizer_plan, optimizer_validation as ov, text_diff

logger = logging.getLogger("content_optimizer")

InputError = competitor_gap.InputError
BusyError = competitor_gap.BusyError

EVIDENCE_MAX_AGE = timedelta(hours=24)
NO_RELIABLE = "No reliable optimization recommendation."
NO_CHANGE = "No change recommended."
# What the existing WordPress connector can write (mirrors routes/wordpress.FIELD_DEPLOYERS;
# a test keeps the two in step). Everything else is approved here and applied by a person.
DEPLOYABLE_CATEGORIES = ("title", "meta_description", "h1")
NOT_DEPLOYABLE_NOTE = "The WordPress connector can only write titles, meta descriptions and H1s. Approve this, then apply it in WordPress yourself."

TYPE_LABELS = {
    "add_section": "Add a missing section", "expand_section": "Expand a weak section", "rewrite_section": "Rewrite an unclear section",
    "improve_heading": "Improve a heading", "improve_title": "Improve the title", "improve_meta_description": "Improve the meta description",
    "add_faq": "Add an FAQ answer", "improve_internal_link": "Add an internal link",
}

_running: set[tuple[int, int]] = set()
_lock = threading.Lock()


def content_hash(text: str) -> str:
    """The same 'is this the same suggestion' comparison the existing suggestion code uses
    (trim, collapse whitespace, casefold, sha256): a test keeps the two identical, so the
    unique (issue_id, content_hash) protection covers optimizer suggestions too."""
    return hashlib.sha256(" ".join((text or "").split()).casefold().encode("utf-8")).hexdigest()


def _kw_key(keyword: str) -> str:
    return " ".join((keyword or "").split()).casefold()


def _utc_naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def issue_key(type_: str, target_ref: str) -> tuple[str, str]:
    """(category, rule) of the Issue that hosts suggestions of this kind for a page. The
    category decides whether the existing deployers can write it."""
    if type_ == "improve_title":
        return "title", "opt_improve_title"
    if type_ == "improve_meta_description":
        return "meta_description", "opt_improve_meta_description"
    if type_ == "improve_heading":
        return ("h1", "opt_improve_h1") if target_ref == "h1" else ("h2", "opt_improve_heading")
    return "content", f"opt_{type_}"


# ── evidence ──────────────────────────────────────────────────────────────

def _matches(run: models.CompetitorAnalysisRun, inputs: dict) -> bool:
    return (optimizer_page.url_key(run.target_url) == optimizer_page.url_key(inputs["target_url"])
            and _kw_key(run.keyword) == _kw_key(inputs["keyword"])
            and (run.location or "").upper() == inputs["location"] and (run.device or "desktop") == inputs["device"])


def find_reusable_run(db, project_id: int, inputs: dict) -> models.CompetitorAnalysisRun | None:
    """A successful analysis of this page for this keyword / market / device made in the
    last 24 hours: reusing it saves a search-results call and a round of page fetches."""
    now = _utc_naive(datetime.now(timezone.utc))
    rows = (db.query(models.CompetitorAnalysisRun)
            .filter(models.CompetitorAnalysisRun.project_id == project_id, models.CompetitorAnalysisRun.status.in_(("ok", "partial")))
            .order_by(models.CompetitorAnalysisRun.created_at.desc(), models.CompetitorAnalysisRun.id.desc()).limit(50).all())
    for r in rows:
        created = _utc_naive(r.created_at)
        if created and now - created <= EVIDENCE_MAX_AGE and _matches(r, inputs):
            return r
    return None


def get_evidence_run(db, project, inputs: dict, evidence_run_id: int | None = None, refresh: bool = False):
    """(analysis run, reused). Uses the given analysis, else a fresh stored one, else
    gathers new evidence (evidence only: the AI action plan is not paid for)."""
    if evidence_run_id is not None:
        run = db.get(models.CompetitorAnalysisRun, evidence_run_id)
        if not run or run.project_id != project.id:
            raise LookupError("That analysis was not found in this project.")
        if not _matches(run, inputs):
            raise InputError("That analysis was made for a different page, keyword, market or device.")
        return run, True
    if not refresh:
        found = find_reusable_run(db, project.id, inputs)
        if found:
            return found, True
    run = competitor_gap.run_analysis(db, project, inputs["target_url"], inputs["keyword"], inputs["location"], inputs["device"], with_plan=False)
    return run, False


def _evidence_material(db, run: models.CompetitorAnalysisRun | None) -> dict:
    if run is None:
        return {"gaps": [], "competitor_texts": [], "competitor_domains": [], "serp_titles": []}
    gaps = [{"id": g.id, "gap_type": g.gap_type, "label": g.label, "target_coverage": g.target_coverage, "competitor_count": g.competitor_count,
             "competitor_total": g.competitor_total, "confidence": g.confidence}
            for g in db.query(models.CompetitorGap).filter(models.CompetitorGap.analysis_run_id == run.id).order_by(models.CompetitorGap.id)]
    snaps = (db.query(models.CompetitorPageSnapshot).filter(models.CompetitorPageSnapshot.analysis_run_id == run.id)
             .order_by(models.CompetitorPageSnapshot.position).all())
    texts = []
    for s in snaps:
        if s.fetch_status != "ok":
            continue
        texts += [t for t in (s.text, s.title, s.h1) if t]
        texts += [h["text"] for h in (s.headings_json or [])[:30] if isinstance(h, dict) and h.get("text")]
    return {
        "gaps": gaps, "competitor_texts": texts,
        "competitor_domains": sorted({normalize_domain(s.url) for s in snaps if s.selected and s.url} - {""}),
        "serp_titles": [s.title for s in snaps if s.selected and s.title][:optimizer_plan.MAX_SERP_TITLES],
    }


def _assemble(db, project, page, evidence_run, keyword: str) -> dict:
    """Everything about the page and its surroundings that both the model call and the
    validation need, loaded once."""
    model = optimizer_page.build_page_model(page)
    material = _evidence_material(db, evidence_run)
    site = optimizer_page.site_pages(db, project.id, page.url)
    labels = [g["label"] for g in material["gaps"] if g["gap_type"] in ("topic", "question")][:12]
    related = optimizer_page.related_pages(site, keyword, labels)
    candidates = optimizer_page.internal_link_candidates(related, model)
    profile = db.query(models.BusinessProfile).filter(models.BusinessProfile.project_id == project.id).first()
    ctx = {
        "keyword": keyword, "page": model, "site_pages": site, "related": related, "candidates": candidates,
        "competitor_texts": material["competitor_texts"], "competitor_domains": material["competitor_domains"], "profile": profile,
    }
    return {"model": model, "material": material, "related": related, "candidates": candidates, "profile": profile, "ctx": ctx}


# ── a run ─────────────────────────────────────────────────────────────────

def _get_page(db, project, page_id) -> models.Page:
    page = db.get(models.Page, page_id) if page_id else None
    if not page or page.project_id != project.id:
        raise LookupError("That page was not found in this project.")
    return page


def _finish(db, run: models.ContentOptimizationRun, status: str, error: str | None = None, notes: dict | None = None):
    run.status, run.error = status, error
    if notes is not None:
        run.notes = notes
    db.commit()
    return run


def run_optimization(db, project, page_id: int, keyword: str, location: str, device: str,
                     evidence_run_id: int | None = None, refresh_evidence: bool = False) -> models.ContentOptimizationRun:
    """Runs one optimization and returns the persisted run. Raises LookupError (no such
    page / analysis), InputError (bad request) or BusyError (already running); every other
    outcome (a failed search provider, too little evidence, an AI failure, nothing worth
    changing) is recorded ON the run."""
    page = _get_page(db, project, page_id)
    inputs = competitor_gap.validate_inputs(project, page.url, keyword, location, device)
    guard = (project.id, page.id)
    with _lock:
        if guard in _running:
            raise BusyError("An optimization is already running for this page. Wait for it to finish.")
        _running.add(guard)
    try:
        evidence_run, reused = get_evidence_run(db, project, inputs, evidence_run_id, refresh_evidence)
        run = models.ContentOptimizationRun(
            project_id=project.id, page_id=page.id, target_url=inputs["target_url"], keyword=inputs["keyword"],
            location=inputs["location"], device=inputs["device"], evidence_run_id=evidence_run.id, evidence_reused=reused,
            status="error", error="The optimization did not finish (it was interrupted).", notes={},
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id
        try:
            _execute(db, run, project, page, evidence_run)
        except Exception as exc:  # noqa: BLE001 -- last resort: per-stage failures are handled inside _execute
            logger.exception("content optimization %s crashed", run_id)
            db.rollback()
            run = db.get(models.ContentOptimizationRun, run_id)
            _finish(db, run, "error", f"Unexpected error: {exc}")
        db.refresh(run)
        return run
    finally:
        with _lock:
            _running.discard(guard)


def _execute(db, run, project, page, evidence_run) -> None:
    # 1. is there evidence at all? A failed or empty search is reported as exactly that.
    if evidence_run.status == "error":
        _finish(db, run, "error", f"The search evidence could not be gathered: {evidence_run.error}")
        return
    if evidence_run.status == "no_data":
        _finish(db, run, "no_data", f"{NO_RELIABLE} {evidence_run.error}")
        return

    # 2. the page, the evidence, and what this page can support
    a = _assemble(db, project, page, evidence_run, run.keyword)
    model, material = a["model"], a["material"]
    evidence = optimizer_plan.build_evidence(material["gaps"], model, run.keyword, material["serp_titles"])
    if not optimizer_plan.has_something_to_fix(evidence):
        _finish(db, run, "no_change", None, {"no_change_reason": "The page already covers what the comparable ranking pages recurringly cover, and its title, meta description and H1 are in place.", "discarded": [], "warnings": []})
        return
    availability = optimizer_plan.availability(model, a["candidates"])
    bundle = {
        "keyword": run.keyword, "location": run.location, "device": run.device, "page": model,
        "serp": {"analyzed": evidence_run.competitors_analyzed, "selected": evidence_run.competitors_selected,
                 "total_results": evidence_run.serp_total_results, "features": evidence_run.serp_features or [],
                 "question_data_available": evidence_run.source != "semrush"},
        "intent": evidence_run.intent, "format_distribution": evidence_run.format_distribution, "evidence": evidence,
        "candidates": a["candidates"], "availability": availability,
        "allowed_types": [t for t, why in availability.items() if why is None], "allowed_targets": optimizer_plan.allowed_targets(model),
    }

    # 3. the model
    try:
        answer = ai_provider.generate_optimizer_suggestions(db, bundle, a["profile"])
    except AIGenerationError as exc:
        logger.warning("optimizer run %s: AI failed: %s", run.id, exc)
        _finish(db, run, "error", f"The AI could not produce usable suggestions: {exc}")
        return

    # 4. resolve against the application's own data
    resolved = optimizer_plan.resolve_suggestions(answer["items"], answer["malformed"], bundle)
    notes = {"discarded": resolved["discarded"], "warnings": resolved["warnings"], "no_change_reason": answer["no_change_reason"]}
    if not resolved["suggestions"]:
        if not answer["items"] and not answer["malformed"]:
            notes["no_change_reason"] = answer["no_change_reason"] or NO_CHANGE
            _finish(db, run, "no_change", None, notes)
        else:
            why = "; ".join(f"{d['id']}: {d['reason']}" for d in resolved["discarded"][:3])
            _finish(db, run, "error", f"The AI proposed {len(answer['items']) + len(answer['malformed'])} edit(s) but none could be used ({why}).", notes)
        return

    # 5. validate and store, through the existing Suggestion workflow
    stored, skipped = _persist(db, run, project, page, resolved["suggestions"], a)
    notes["discarded"] = resolved["discarded"] + skipped
    if not stored:
        _finish(db, run, "error", "Every suggestion repeated wording that was already proposed for this page, so nothing new was stored.", notes)
        return
    _finish(db, run, "ok", None, notes)


# ── storing (through the existing Suggestion workflow) ────────────────────

def _get_or_create_issue(db, project, page, type_: str, target_ref: str) -> models.Issue:
    category, rule = issue_key(type_, target_ref)
    issue = db.query(models.Issue).filter(models.Issue.page_id == page.id, models.Issue.category == category, models.Issue.rule == rule).first()
    if issue is None:
        issue = models.Issue(project_id=project.id, page_id=page.id, category=category, rule=rule, severity="info",
                             message=f"AI Content Optimizer: {TYPE_LABELS[type_].lower()}")
        db.add(issue)
        db.flush()
    return issue


def _clear_pending(db, run: models.ContentOptimizationRun) -> None:
    """Regeneration replaces what nobody has decided on, for THIS page and keyword only.
    Accepted / edited / deployed suggestions are recorded human decisions and are never
    touched; rejected ones are kept too, so the same wording is not offered again."""
    older = [r.id for r in db.query(models.ContentOptimizationRun).filter(
        models.ContentOptimizationRun.project_id == run.project_id, models.ContentOptimizationRun.page_id == run.page_id,
        models.ContentOptimizationRun.id != run.id) if _kw_key(r.keyword) == _kw_key(run.keyword)]
    if not older:
        return
    rows = (db.query(models.SuggestionOptimization, models.Suggestion)
            .join(models.Suggestion, models.Suggestion.id == models.SuggestionOptimization.suggestion_id)
            .filter(models.SuggestionOptimization.run_id.in_(older), models.Suggestion.status == "pending").all())
    for meta, suggestion in rows:
        db.delete(meta)
        db.delete(suggestion)
    db.flush()


def _target_label(model: dict, type_: str, target_ref: str) -> str:
    if target_ref == "title":
        return "Title"
    if target_ref == "meta_description":
        return "Meta description"
    if target_ref == "h1":
        return "H1"
    section = optimizer_page.find_section(model, target_ref)
    if section:
        return f"Section: {section['heading']}"
    return "New section" if type_ == "add_section" else "New FAQ entry" if type_ == "add_faq" else "New content"


def _persist(db, run, project, page, suggestions: list[dict], a: dict) -> tuple[int, list[dict]]:
    _clear_pending(db, run)
    stored, skipped = 0, []
    try:
        for s in suggestions:
            issue = _get_or_create_issue(db, project, page, s["type"], s["target_ref"])
            h = content_hash(s["after"])
            existing = db.query(models.Suggestion).filter(models.Suggestion.issue_id == issue.id, models.Suggestion.content_hash == h).first()
            if existing:
                skipped.append({"id": s["id"], "reason": f"the same wording was already proposed for this page (its status: {existing.status})"})
                continue
            validation = ov.validate_suggestion(s, a["ctx"])
            fact = next((c for c in validation["checks"] if c["name"] == "fact_check"), None)
            row = models.Suggestion(
                project_id=project.id, page_id=page.id, issue_id=issue.id, content=s["after"], content_hash=h,
                rank=db.query(models.Suggestion).filter(models.Suggestion.issue_id == issue.id).count() + 1, status="pending",
            )
            db.add(row)
            db.flush()
            db.add(models.SuggestionOptimization(
                suggestion_id=row.id, run_id=run.id, project_id=project.id, page_id=page.id, suggestion_type=s["type"],
                target_ref=s["target_ref"], target_label=_target_label(a["model"], s["type"], s["target_ref"]), priority=s["priority"],
                problem=s["problem"], evidence_json=s["evidence"], before_content=s["before"], link_target=s["link_target"],
                confidence=s["confidence"], requires_fact_check=bool(fact and fact["status"] != "ok"), claims_to_verify=validation["claims"],
                validation_status=validation["status"], validation_json={"status": validation["status"], "checks": validation["checks"]},
            ))
            stored += 1
        db.commit()
    except IntegrityError:
        db.rollback()
        raise
    return stored, skipped


# ── editing: the same checks run again on a person's text ─────────────────

def _meta_for(db, suggestion_id: int) -> models.SuggestionOptimization | None:
    return db.query(models.SuggestionOptimization).filter(models.SuggestionOptimization.suggestion_id == suggestion_id).first()


def is_optimizer_suggestion(db, suggestion_id: int) -> bool:
    return _meta_for(db, suggestion_id) is not None


def revalidate(db, suggestion: models.Suggestion, new_text: str) -> dict | None:
    """The validation of `new_text` as this suggestion's text, or None when it is not an
    optimizer suggestion. A person's edit is held to the same checks as the AI's draft:
    it may not be approved if it is blocked."""
    meta = _meta_for(db, suggestion.id)
    if meta is None:
        return None
    project, page = db.get(models.Project, meta.project_id), db.get(models.Page, meta.page_id)
    run = db.get(models.ContentOptimizationRun, meta.run_id) if meta.run_id else None
    evidence_run = db.get(models.CompetitorAnalysisRun, run.evidence_run_id) if run and run.evidence_run_id else None
    a = _assemble(db, project, page, evidence_run, run.keyword if run else "")
    s = {"type": meta.suggestion_type, "target_ref": meta.target_ref, "before": meta.before_content, "after": new_text, "problem": meta.problem,
         "evidence": meta.evidence_json or [], "claims_to_verify": [], "requires_fact_check": False, "link_target": meta.link_target}
    return ov.validate_suggestion(s, a["ctx"])


def apply_validation(db, suggestion_id: int, validation: dict) -> None:
    meta = _meta_for(db, suggestion_id)
    fact = next((c for c in validation["checks"] if c["name"] == "fact_check"), None)
    meta.validation_status = validation["status"]
    meta.validation_json = {"status": validation["status"], "checks": validation["checks"]}
    meta.claims_to_verify = validation["claims"]
    meta.requires_fact_check = bool(fact and fact["status"] != "ok")


def blocking_reasons(db, suggestion_id: int) -> list[str] | None:
    """None when the suggestion may be approved / deployed (or is not an optimizer
    suggestion); else the messages of the checks that block it."""
    meta = _meta_for(db, suggestion_id)
    if meta is None or ov.is_ready_for_approval({"status": meta.validation_status}):
        return None
    checks = (meta.validation_json or {}).get("checks", [])
    return [f"{c['name']}: {c.get('message', c['status'])}" for c in checks if c["status"] in ("blocked", "error")] or [f"validation status is {meta.validation_status}"]


# ── reading ───────────────────────────────────────────────────────────────

def evidence_text(e: dict) -> str:
    """The plain-language line for one piece of stored evidence, built from the stored
    numbers (never from the model)."""
    n, total, cov = e.get("competitor_count", 0), e.get("competitor_total", 0), e.get("target_coverage")
    mine = f" Your page: {cov}." if cov and cov != "n/a" else ""
    t = e["type"]
    if t == "topic_consensus":
        return f"{n} of {total} comparable ranking pages cover “{e['label']}”.{mine}"
    if t == "question_consensus":
        return f"{n} of {total} comparable ranking pages answer “{e['label']}”.{mine}"
    if t == "query_coverage":
        return f"{n} of {total} comparable ranking pages use the phrase “{e['label']}”.{mine}"
    if t in ("intent", "serp_format", "page_fact"):
        # these labels are already full sentences written by the application
        return f"{e['label'].rstrip('.')}."
    return e["label"]


def suggestion_view(db, meta: models.SuggestionOptimization, s: models.Suggestion, category: str, live_map: dict, number: int | None = None) -> dict:
    final = s.edited_content or s.content
    validation = meta.validation_json or {"status": meta.validation_status, "checks": []}
    deployable = category in DEPLOYABLE_CATEGORIES
    return {
        "suggestion_id": s.id, "number": number, "type": meta.suggestion_type, "type_label": TYPE_LABELS.get(meta.suggestion_type, meta.suggestion_type),
        "target_ref": meta.target_ref, "target_label": meta.target_label, "priority": meta.priority, "confidence": meta.confidence,
        "problem": meta.problem, "evidence": [{**e, "text": evidence_text(e)} for e in meta.evidence_json or []],
        "before": meta.before_content, "after": s.content, "edited_content": s.edited_content, "final_text": final, "link_target": meta.link_target,
        "diff_html": text_diff.diff_html(meta.before_content, final), "status": s.status, "category": category,
        "requires_fact_check": bool(meta.requires_fact_check), "claims_to_verify": meta.claims_to_verify or [],
        "validation": validation, "ready": ov.is_ready_for_approval({"status": meta.validation_status}),
        "deployable": deployable, "deploy_note": None if deployable else NOT_DEPLOYABLE_NOTE,
        "live": deploy_status.suggestion_live_fields(live_map, s),
    }


def _views(db, rows) -> list[dict]:
    live_map = deploy_status.live_status_for_suggestions(db, [s.id for _, s, _ in rows if s.status == "deployed"])
    return [suggestion_view(db, meta, s, issue.category, live_map, number=n) for n, (meta, s, issue) in enumerate(rows, start=1)]


def _rows(db, *filters):
    return (db.query(models.SuggestionOptimization, models.Suggestion, models.Issue)
            .join(models.Suggestion, models.Suggestion.id == models.SuggestionOptimization.suggestion_id)
            .join(models.Issue, models.Issue.id == models.Suggestion.issue_id)
            .filter(*filters).order_by(models.SuggestionOptimization.id).all())


def summary_line(run: models.ContentOptimizationRun, count: int = 0) -> str:
    if run.status == "ok":
        return f"{count} suggestion{'s' if count != 1 else ''} to review"
    if run.status == "no_change":
        return NO_CHANGE
    if run.status == "no_data":
        return NO_RELIABLE
    return "The optimization failed."


def recent_runs(db, project_id: int, limit: int = 10) -> list[dict]:
    runs = (db.query(models.ContentOptimizationRun).filter(models.ContentOptimizationRun.project_id == project_id)
            .order_by(models.ContentOptimizationRun.created_at.desc(), models.ContentOptimizationRun.id.desc()).limit(limit).all())
    counts = {}
    for r in runs:
        counts[r.id] = db.query(models.SuggestionOptimization).filter(models.SuggestionOptimization.run_id == r.id).count()
    return [{"id": r.id, "keyword": r.keyword, "target_url": r.target_url, "status": r.status, "error": r.error,
             "summary_line": summary_line(r, counts[r.id]), "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else None} for r in runs]


def run_detail(db, run: models.ContentOptimizationRun) -> dict:
    views = _views(db, _rows(db, models.SuggestionOptimization.run_id == run.id))
    earlier = _views(db, _rows(
        db, models.SuggestionOptimization.page_id == run.page_id, models.SuggestionOptimization.run_id != run.id,
        models.Suggestion.status.in_(("accepted", "edited", "deployed"))))
    ev = db.get(models.CompetitorAnalysisRun, run.evidence_run_id) if run.evidence_run_id else None
    notes = run.notes or {}
    return {
        "id": run.id, "project_id": run.project_id, "page_id": run.page_id, "target_url": run.target_url, "keyword": run.keyword,
        "location": run.location, "device": run.device, "status": run.status, "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None, "summary_line": summary_line(run, len(views)),
        "evidence": None if ev is None else {
            "run_id": ev.id, "reused": bool(run.evidence_reused), "status": ev.status, "summary_line": competitor_gap.summary_line(ev),
            "notices": competitor_gap.notices(ev), "created_at": ev.created_at.isoformat() if ev.created_at else None, "source": ev.source,
        },
        "suggestions": views, "earlier_decisions": earlier,
        "discarded": notes.get("discarded") or [], "warnings": notes.get("warnings") or [], "no_change_reason": notes.get("no_change_reason"),
    }


def selectable_pages(db, project_id: int, limit: int = 300) -> list[dict]:
    """The project's pages for the selector: one entry per URL (the same page can exist
    once per data source), preferring the one whose section text is known."""
    best: dict[str, models.Page] = {}
    for p in db.query(models.Page).filter(models.Page.project_id == project_id).order_by(models.Page.updated_at.desc(), models.Page.id.desc()).all():
        key = optimizer_page.url_key(p.url)
        if key and (key not in best or (not (best[key].markdown or best[key].fit_markdown) and (p.markdown or p.fit_markdown))):
            best[key] = p
    return [{"id": p.id, "url": p.url, "title": optimizer_page.clean(p.title) or p.url} for p in sorted(best.values(), key=lambda x: x.url)[:limit]]
