"""
Between the evidence and the model's answer, for the AI Content Optimizer. No network,
no model, no database.

The property everything here protects: the model can never put a fact, a count or a
"before" text into a stored suggestion.

* It receives a NUMBERED evidence list (E01, E02, ...) built from the application's own
  data and may only cite ids. Each cited id is expanded back into its label and counts
  from that list; an id that does not exist is dropped, and a suggestion left with no
  valid evidence is discarded.
* It names WHAT it wants to change by an id ("title", "meta_description", "h1",
  "sec_04", "new"). The application looks up the current text itself, so a before/after
  pair can never show text the model made up.
* One malformed, unsupported or unsupported-for-this-page suggestion is discarded on its
  own, with its reason, and the rest of the answer is kept. Nothing is dropped silently.

What the model proposes is then validated by services/optimizer_validation.py.
"""
from pydantic import ValidationError

from .. import schemas
from . import action_plan, optimizer_page, optimizer_validation as ov

MAX_SUGGESTIONS = 5
MAX_GAP_EVIDENCE = 25
MAX_SERP_TITLES = 5
MAX_CLAIMS = 12
# The spec's signal priority: intent, then SERP format, then recurring topics, then
# People-Also-Ask questions, then related searches / keyword coverage. Word count is
# never evidence.
SIGNAL_ORDER = {"intent": 0, "format": 1, "topic": 2, "question": 3, "query": 4}
COVERAGE_EVIDENCE = ("topic_consensus", "question_consensus", "query_coverage")
_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
_CONF_STRENGTH = {"high": 0.8, "medium": 0.5, "low": 0.2}


# ── evidence ──────────────────────────────────────────────────────────────

def _page_facts(model: dict, keyword: str) -> list[dict]:
    """Facts about the page itself, computed here (never by the model): a missing title,
    meta description or H1, or one that does not contain the keyword's words. `critical`
    marks an element that is simply absent."""
    facts = optimizer_page.keyword_facts(model, keyword)
    out = []

    def add(label: str, coverage: str, critical: bool):
        out.append({"type": "page_fact", "label": label, "competitor_count": 0, "competitor_total": 0,
                    "target_coverage": coverage, "confidence": "high", "gap_id": None, "critical": critical})

    if model["title"] is None:
        add("The page has no title", "missing", True)
    elif not facts["title"]:
        add("The title does not contain the target keyword", "partial", False)
    if model["meta_description"] is None:
        add("The page has no meta description", "missing", True)
    elif not facts["meta_description"]:
        add("The meta description does not contain the target keyword", "partial", False)
    if model["h1"] is None:
        add("The page has no H1", "missing", True)
    elif model["h1_count"] > 1:
        add(f"The page has {model['h1_count']} H1 elements", "partial", False)
    elif not facts["h1"]:
        add("The H1 does not contain the target keyword", "partial", False)
    return out


def build_evidence(gaps: list[dict], model: dict, keyword: str, serp_titles=()) -> list[dict]:
    """The numbered, citable evidence: gap rows in the spec's signal order, then page
    facts, then the top results' titles. Each item: id, type, label, competitor_count,
    competitor_total, target_coverage, confidence, gap_id, critical."""
    items = []
    for g in sorted(gaps, key=lambda g: SIGNAL_ORDER.get(g["gap_type"], 9))[:MAX_GAP_EVIDENCE]:
        items.append({
            "type": action_plan.EVIDENCE_TYPE_BY_GAP.get(g["gap_type"], g["gap_type"]), "label": g["label"],
            "competitor_count": int(g.get("competitor_count") or 0), "competitor_total": int(g.get("competitor_total") or 0),
            "target_coverage": g.get("target_coverage") or "n/a", "confidence": g.get("confidence") or "low",
            "gap_id": g.get("id"), "critical": False,
        })
    items += _page_facts(model, keyword)
    titles = [optimizer_page.clean(t)[:90] for t in serp_titles if t and optimizer_page.clean(t)][:MAX_SERP_TITLES]
    if titles:
        items.append({"type": "serp_titles", "label": "Titles of the top comparable results: " + " | ".join(titles),
                      "competitor_count": len(titles), "competitor_total": len(titles), "target_coverage": "n/a",
                      "confidence": "medium", "gap_id": None, "critical": False})
    for n, e in enumerate(items, start=1):
        e["id"] = f"E{n:02d}"
    return items


def has_something_to_fix(evidence: list[dict]) -> bool:
    """False when the page already covers everything the comparable pages recurringly
    cover AND its title / meta description / H1 are in place: nothing here calls for a
    model call, and 'No change recommended' is the honest answer."""
    for e in evidence:
        if e["type"] in COVERAGE_EVIDENCE and e["target_coverage"] != "covered":
            return True
        if e["type"] in ("intent", "serp_format") and e["target_coverage"] not in ("covered", "n/a"):
            return True
        if e["type"] == "page_fact":
            return True
    return False


# ── what this page can support ────────────────────────────────────────────

def availability(model: dict, candidates: list[dict]) -> dict[str, str | None]:
    """{type: None when the type is available for this page, else the reason it is not}.
    The optimizer never offers to edit something it cannot see."""
    has_text = any(s["text"] for s in model["sections"])
    return {
        "improve_title": None,
        "improve_meta_description": None,
        "improve_heading": None if (model["h1"] or model["sections"]) else "the page has no headings",
        "expand_section": None if has_text else "the text of the page's sections is not available (the page was not crawled with its content)",
        "rewrite_section": None if has_text else "the text of the page's sections is not available (the page was not crawled with its content)",
        "add_section": None if model["content_known"] else "the page's content is not known",
        "add_faq": None if model["content_known"] else "the page's content is not known",
        "improve_internal_link": (
            "the page's content is not known" if not model["content_known"]
            else None if candidates else "no other page of the site relates to this topic"),
    }


def available_types(model: dict, candidates: list[dict]) -> list[str]:
    return [t for t, why in availability(model, candidates).items() if why is None]


def allowed_targets(model: dict) -> list[str]:
    return ["title", "meta_description", "h1"] + [s["id"] for s in model["sections"]] + ["new"]


# ── parsing the model's answer ────────────────────────────────────────────

def _clean(text, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def parse_output(raw: str) -> tuple[list[schemas.ModelOptimizerSuggestion], list[dict], str | None]:
    """(suggestions, malformed, no_change_reason). Raises action_plan.PlanFormatError only
    when the answer as a whole is unusable (not JSON, or not an object with a list): the
    caller retries once. One malformed suggestion inside a good answer is reported in
    `malformed` and does not void the rest."""
    data = action_plan.json_object(raw)
    try:
        envelope = schemas.ModelOptimizerOutput.model_validate(data)
    except ValidationError as exc:
        err = exc.errors()[0]
        raise action_plan.PlanFormatError(f"the response does not match the optimizer schema: {err['msg']} at {err['loc']}") from None
    items, malformed = [], []
    for n, raw_item in enumerate(envelope.suggestions, start=1):
        try:
            items.append(schemas.ModelOptimizerSuggestion.model_validate(raw_item))
        except ValidationError as exc:
            err = exc.errors()[0]
            malformed.append({"id": f"proposal {n}", "reason": f"malformed: {err['msg']} ({'.'.join(str(p) for p in err['loc'])})"})
    return items, malformed, (_clean(envelope.no_change_reason, 500) or None)


# ── resolving it against the application's own data ───────────────────────

def _strength(cited: list[dict]) -> float:
    """How strongly the cited evidence supports acting, 0..1, from the application's
    numbers only."""
    best = 0.0
    for e in cited:
        if e["type"] in COVERAGE_EVIDENCE:
            v = e["competitor_count"] / e["competitor_total"] if e["competitor_total"] else 0.0
        elif e["type"] == "page_fact":
            v = 1.0 if e.get("critical") else 0.4
        elif e["type"] in ("intent", "serp_format"):
            v = _CONF_STRENGTH.get(e.get("confidence"), 0.2)
        else:
            v = 0.3
        best = max(best, v)
    return best


def _resolve_before(t: str, target: str, model: dict) -> tuple[str | None, str | None]:
    """(before, problem). The current text is looked up here, never taken from the model."""
    if target == "title":
        return model["title"], None
    if target == "meta_description":
        return model["meta_description"], None
    if target == "h1":
        return model["h1"], None
    if target.startswith("sec_"):
        section = optimizer_page.find_section(model, target)
        if section is None:
            return None, f"the page has no section {target}"
        if t == "improve_heading":
            return section["heading"], None
        if t in ("expand_section", "rewrite_section"):
            if section["text"] is None:
                return None, "the current text of that section is not available"
            return section["text"], None
    return None, None


def resolve_suggestions(items: list[schemas.ModelOptimizerSuggestion], malformed: list[dict], bundle: dict) -> dict:
    """{"suggestions": [strict OptimizerSuggestion dicts], "discarded": [{id, reason}],
    "warnings": [...]}. bundle needs: evidence, page (optimizer_page model), availability."""
    by_id = {e["id"]: e for e in bundle["evidence"]}
    model, avail = bundle["page"], bundle["availability"]
    targets = set(allowed_targets(model))
    discarded, warnings, staged = list(malformed), [], []

    for n, it in enumerate(items, start=1):
        ref = f"proposal {n}"
        t, target = (it.type or "").strip().lower(), (it.target or "").strip().lower()
        if t not in ov.TYPES:
            discarded.append({"id": ref, "reason": f"unsupported suggestion type {it.type!r}"})
            continue
        if avail.get(t):
            discarded.append({"id": ref, "reason": f"{t.replace('_', ' ')} is not available for this page: {avail[t]}"})
            continue
        if target not in targets or not ov.target_fits(t, target):
            discarded.append({"id": ref, "reason": f"the target {it.target!r} does not fit a {t.replace('_', ' ')} on this page"})
            continue
        after = (it.after or "").strip()
        if not after:
            discarded.append({"id": ref, "reason": "it proposes no text"})
            continue
        problem = _clean(it.problem, 1500)
        if not problem:
            discarded.append({"id": ref, "reason": "it does not say what the problem is"})
            continue
        bad = action_plan.forbidden_reason(problem)
        if bad:
            discarded.append({"id": ref, "reason": f"the stated problem {bad}"})
            continue

        cited, unknown = [], []
        for eid in dict.fromkeys(str(x).strip() for x in it.evidence_ids):
            if eid in by_id:
                cited.append(by_id[eid])
            else:
                unknown.append(eid)
        if unknown:
            warnings.append(f"{ref}: ignored unknown evidence id(s) {', '.join(unknown)}")
        if not cited:
            discarded.append({"id": ref, "reason": "it cites no evidence from the supplied list"})
            continue
        coverage = [e for e in cited if e["type"] in COVERAGE_EVIDENCE]
        if t in ("add_section", "add_faq", "expand_section") and coverage and len(coverage) == len(cited) \
                and all(e["target_coverage"] == "covered" for e in coverage):
            discarded.append({"id": ref, "reason": f"it recommends {t.replace('_', ' ')} but every cited item is already covered by the page"})
            continue

        before, why = _resolve_before(t, target, model)
        if why:
            discarded.append({"id": ref, "reason": why})
            continue

        strength = _strength(cited)
        priority = it.priority if it.priority in _PRIORITY_RANK else "medium"
        if priority == "high" and strength < 0.5:
            priority = "medium" if strength >= 0.34 else "low"
            warnings.append(f"{ref}: priority lowered from high (the cited evidence is not strong enough)")
        top_conf = max((_CONFIDENCE_RANK.get(e.get("confidence"), 0) for e in cited), default=0)
        stated = _CONFIDENCE_RANK.get(it.confidence, 0)
        confidence = ["low", "medium", "high"][min(stated, top_conf)]
        claims = [_clean(c, 300) for c in it.claims_to_verify if c and _clean(c, 300)][:MAX_CLAIMS]
        staged.append({
            "type": t, "target_ref": target, "priority": priority, "problem": problem, "before": before, "after": after,
            "link_target": (it.link_target or "").strip() or None if t == "improve_internal_link" else None,
            "requires_fact_check": bool(it.requires_fact_check), "claims_to_verify": claims, "confidence": confidence,
            "evidence": [{"type": e["type"], "label": e["label"], "competitor_count": e["competitor_count"],
                          "competitor_total": e["competitor_total"], "target_coverage": e["target_coverage"], "gap_id": e["gap_id"]} for e in cited],
            "_strength": strength, "_ref": ref,
        })

    staged.sort(key=lambda s: (_PRIORITY_RANK[s["priority"]], -s["_strength"]))
    seen, kept = set(), []
    for s in staged:
        key = (s["type"], s["target_ref"])
        if key in seen:
            discarded.append({"id": s["_ref"], "reason": f"a stronger suggestion for the same {s['type'].replace('_', ' ')} target was kept"})
            continue
        seen.add(key)
        kept.append(s)
    if len(kept) > MAX_SUGGESTIONS:
        warnings.append(f"kept the {MAX_SUGGESTIONS} strongest of {len(kept)} suggestions")
        for extra in kept[MAX_SUGGESTIONS:]:
            discarded.append({"id": extra["_ref"], "reason": "over the limit of 5 suggestions per run"})
        kept = kept[:MAX_SUGGESTIONS]

    strict = []
    for n, s in enumerate(kept, start=1):
        s.pop("_strength"), s.pop("_ref")
        try:
            strict.append(schemas.OptimizerSuggestion(id=f"sug_{n:03d}", **s).model_dump())
        except ValidationError as exc:
            err = exc.errors()[0]
            discarded.append({"id": f"sug_{n:03d}", "reason": f"invalid: {err['msg']} ({'.'.join(str(p) for p in err['loc'])})"})
    return {"suggestions": strict, "discarded": discarded, "warnings": warnings}
