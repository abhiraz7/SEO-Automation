"""
Turning gaps into a citable evidence list, and validating what the AI says about it.

The single most important property: the model can NEVER put a fact into a stored
plan. It receives a numbered evidence list (E01, E02, ...) built from the
application's own gap rows and may only cite those ids. Each cited id is expanded
here back into {type, label, competitor_count, competitor_total} from the
application's data. An id that does not exist is dropped; an action left with no
valid evidence is rejected. So "the model invented that 6 of 7 pages cover this"
cannot happen -- there is nowhere for that number to come from.

On top of that, deterministic guards refuse the specific claims the product must
never make: "Google requires...", keyword-density or word-count targets, keyword
stuffing, copying competitors. A model that produces them gets that ACTION rejected
(with the reason recorded), never silently fixed.

Nothing here calls a model or the network.
"""
import json
import re

from pydantic import ValidationError

from .. import schemas

EVIDENCE_TYPE_BY_GAP = {
    "topic": "topic_consensus",
    "question": "question_consensus",
    "query": "query_coverage",
    "intent": "intent",
    "format": "serp_format",
}
_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
MAX_ACTIONS = 8
CHANGE_ACTIONS = ("add", "expand", "rewrite", "restructure", "separate_page")

# Claims the product must not make, whoever makes them. Each entry: (regex, reason).
_FORBIDDEN = [
    (re.compile(r"\b(google|search engines?)\s+(requires?|demands?|mandates?|needs?|expects?)\b", re.I), "claims Google requires something"),
    (re.compile(r"\b(required|mandatory|necessary)\s+(by|for)\s+(google|ranking|seo)\b", re.I), "claims something is required for ranking"),
    (re.compile(r"\bkeyword\s+density\b", re.I), "recommends a keyword-density target"),
    (re.compile(r"\bkeyword\s+stuffing\b|\bstuff(ing)?\s+(the\s+)?keywords?\b", re.I), "mentions keyword stuffing as an approach"),
    (re.compile(r"\b(reach|hit|match|exceed|beat|at least|minimum of|target(ing)?)\s+(a\s+)?(word count of\s+)?\d[\d,]{2,}\s*(\+\s*)?words?\b", re.I), "sets a word-count target"),
    (re.compile(r"\bword\s+count\s+(of|target|goal)\b", re.I), "sets a word-count target"),
    (re.compile(r"\b(copy|reuse|reproduce|paraphrase)\s+(the\s+)?(text|content|wording|copy|sections?)\s+(from|of)\s+(the\s+)?competitors?\b", re.I), "recommends copying competitor content"),
]
_FACTUAL = re.compile(r"\d|%|₹|\$|€|£|\b(19|20)\d\d\b|\b(percent|crore|lakh|million|billion)\b", re.I)


class PlanFormatError(ValueError):
    """The model's answer is not usable JSON for the schema (the caller retries once)."""


# ── evidence list ─────────────────────────────────────────────────────────

def build_evidence_list(gaps: list[dict]) -> list[dict]:
    """Numbered, citable evidence from gap rows (dicts with at least gap_type, label,
    competitor_count, competitor_total, target_coverage, confidence; `id` is the
    database id of the gap when there is one)."""
    out = []
    for n, g in enumerate(gaps, start=1):
        out.append({
            "id": f"E{n:02d}",
            "type": EVIDENCE_TYPE_BY_GAP.get(g["gap_type"], g["gap_type"]),
            "label": g["label"],
            "competitor_count": int(g.get("competitor_count") or 0),
            "competitor_total": int(g.get("competitor_total") or 0),
            "target_coverage": g.get("target_coverage") or "n/a",
            "confidence": g.get("confidence") or "low",
            "gap_id": g.get("id"),
        })
    return out


# ── parsing ───────────────────────────────────────────────────────────────

def _strip_fences(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    return text


def _json_object(raw: str) -> dict:
    text = _strip_fences(raw)
    try:
        data = json.loads(text)
    except ValueError:
        # Tolerate prose around the object: take the outermost {...}.
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise PlanFormatError("the response is not JSON") from None
        try:
            data = json.loads(text[start:end + 1])
        except ValueError as exc:
            raise PlanFormatError(f"the response is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise PlanFormatError("the response is not a JSON object")
    return data


def parse_action_plan(raw: str) -> schemas.ModelActionPlan:
    try:
        return schemas.ModelActionPlan.model_validate(_json_object(raw))
    except ValidationError as exc:
        raise PlanFormatError(f"the response does not match the action-plan schema: {exc.errors()[0]['msg']} at {exc.errors()[0]['loc']}") from None


def parse_gap_draft(raw: str) -> schemas.ModelGapDraft:
    try:
        return schemas.ModelGapDraft.model_validate(_json_object(raw))
    except ValidationError as exc:
        raise PlanFormatError(f"the response does not match the draft schema: {exc.errors()[0]['msg']}") from None


# ── validation ────────────────────────────────────────────────────────────

def _clean(text: str, limit: int) -> str:
    return " ".join((text or "").split())[:limit]


def _forbidden_reason(*texts: str) -> str | None:
    for text in texts:
        for pattern, reason in _FORBIDDEN:
            if pattern.search(text or ""):
                return reason
    return None


# Public names for the AI Content Optimizer, which applies the same product rules
# ("Google requires...", keyword-density / word-count targets, copying competitors)
# and the same factual-statement detector, so both features stay in step.
forbidden_reason = _forbidden_reason
FACTUAL_PATTERN = _FACTUAL


def validate_action_plan(parsed: schemas.ModelActionPlan, evidence: list[dict]) -> dict:
    """{"status": "ok"|"no_data", "actions": [...], "rejected": [{"id","reason"}], "warnings": [...]}.
    `actions` follow the product's plan shape, with every evidence item rebuilt from
    `evidence` (the application's data)."""
    index = {e["id"]: e for e in evidence}
    actions, rejected, warnings = [], [], []

    for i, a in enumerate(parsed.actions, start=1):
        ref = a.id or f"#{i}"
        title, problem, recommendation = _clean(a.title, 120), _clean(a.problem, 400), _clean(a.recommendation, 600)
        if not title or not recommendation:
            rejected.append({"id": ref, "reason": "empty title or recommendation"})
            continue

        bad = _forbidden_reason(title, problem, recommendation)
        if bad:
            rejected.append({"id": ref, "reason": bad})
            continue

        cited, unknown = [], []
        for eid in dict.fromkeys(a.evidence_ids):  # de-duplicate, keep order
            (cited if eid in index else unknown).append(index.get(eid) or eid)
        if unknown:
            warnings.append(f"{ref}: ignored unknown evidence id(s) {', '.join(map(str, unknown))}")

        if not cited and a.type != "leave_unchanged":
            rejected.append({"id": ref, "reason": "cites no evidence from the supplied list"})
            continue

        coverages = {e["target_coverage"] for e in cited}
        if cited and a.type in CHANGE_ACTIONS and coverages == {"covered"}:
            rejected.append({"id": ref, "reason": f"recommends '{a.type}' but every cited item is already covered by the page"})
            continue
        if cited and a.type == "leave_unchanged" and coverages <= {"missing"}:
            rejected.append({"id": ref, "reason": "says leave unchanged but every cited item is missing from the page"})
            continue

        best_ratio = max((e["competitor_count"] / e["competitor_total"] for e in cited if e["competitor_total"]), default=0.0)
        evidence_conf = max((_CONFIDENCE_RANK[e["confidence"]] for e in cited), default=0)
        priority = a.priority
        if priority == "high" and (best_ratio < 0.5 or evidence_conf < 1):
            priority = "medium" if best_ratio >= 0.34 else "low"
            warnings.append(f"{ref}: priority lowered from high (the cited pattern is not strong enough)")
        # The stated confidence can never exceed what the cited evidence supports.
        evidence_cap = ["low", "medium", "high"][evidence_conf]
        confidence = min((a.confidence, evidence_cap), key=lambda c: _CONFIDENCE_RANK[c]) if cited else "low"

        needs_check = a.requires_fact_check or (a.type in ("add", "expand", "rewrite") and bool(_FACTUAL.search(f"{problem} {recommendation}")))
        actions.append({
            "type": a.type,
            "priority": priority,
            "title": title,
            "problem": problem,
            "recommendation": recommendation,
            "evidence": [
                {"type": e["type"], "label": e["label"], "competitor_count": e["competitor_count"],
                 "competitor_total": e["competitor_total"], "gap_id": e["gap_id"]}
                for e in cited
            ],
            "confidence": confidence,
            "requires_fact_check": needs_check,
            "_strength": best_ratio,
        })

    actions.sort(key=lambda x: (_PRIORITY_RANK[x["priority"]], -x["_strength"]))
    if len(actions) > MAX_ACTIONS:
        warnings.append(f"kept the {MAX_ACTIONS} strongest of {len(actions)} actions")
        actions = actions[:MAX_ACTIONS]
    for n, x in enumerate(actions, start=1):
        x["id"] = f"action_{n:03d}"
        x.pop("_strength", None)
    return {"status": "ok" if actions else "no_data", "actions": actions, "rejected": rejected, "warnings": warnings}


# ── drafts ────────────────────────────────────────────────────────────────

_THIN = re.compile(r"^\s*(add|include|provide)\s+more\s+(detail|details|information|content)\.?\s*$", re.I)
MIN_DRAFT_CHARS = 40
MAX_DRAFT_CHARS = 3000
COPY_WINDOW_WORDS = 12


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", (text or "").lower(), re.UNICODE)


def longest_shared_run(draft_words: list[str], source_words: list[str]) -> int:
    """Longest run of consecutive words that appears in both texts (case-insensitive).
    Used as a duplication-RISK signal, never called plagiarism."""
    if not draft_words or not source_words:
        return 0
    positions: dict[str, list[int]] = {}
    for j, w in enumerate(source_words):
        positions.setdefault(w, []).append(j)
    best = 0
    for i, w in enumerate(draft_words):
        for j in positions.get(w, ()):
            k = 0
            while i + k < len(draft_words) and j + k < len(source_words) and draft_words[i + k] == source_words[j + k]:
                k += 1
            best = max(best, k)
    return best


def validate_draft(parsed: schemas.ModelGapDraft, competitor_texts: list[str]) -> dict:
    """{"ok": bool, "draft", "claims_to_verify", "warnings", "error"}. A draft that is
    empty, thin, oversized or that reproduces a run of competitor wording is refused;
    factual-looking sentences are always surfaced for a human to verify (never marked
    verified)."""
    draft = parsed.draft.strip()
    warnings: list[str] = []
    if len(draft) < MIN_DRAFT_CHARS or _THIN.match(draft):
        return {"ok": False, "error": "the draft is too thin to use (it must contain actual proposed text)", "draft": draft, "claims_to_verify": [], "warnings": []}
    if len(draft) > MAX_DRAFT_CHARS:
        return {"ok": False, "error": f"the draft is longer than {MAX_DRAFT_CHARS} characters; an atomic draft should be one focused piece", "draft": draft[:200], "claims_to_verify": [], "warnings": []}
    bad = _forbidden_reason(draft)
    if bad:
        return {"ok": False, "error": f"the draft {bad}", "draft": draft, "claims_to_verify": [], "warnings": []}

    dw = _words(draft)
    worst = max((longest_shared_run(dw, _words(t)) for t in competitor_texts), default=0)
    if worst >= COPY_WINDOW_WORDS:
        return {"ok": False, "error": f"the draft reproduces {worst} consecutive words of a competitor page; it must be original wording", "draft": draft, "claims_to_verify": [], "warnings": []}

    claims = [c.strip() for c in parsed.claims_to_verify if c and c.strip()]
    for sentence in re.split(r"(?<=[.!?।])\s+", draft):
        if _FACTUAL.search(sentence) and sentence.strip() not in claims:
            claims.append(sentence.strip())
    if claims:
        warnings.append(f"{len(claims)} statement(s) contain figures or dates and need a human to verify them before use")
    return {"ok": True, "draft": draft, "claims_to_verify": claims[:12], "warnings": warnings, "error": None}
