"""
Chooses between Semrush and DataForSEO for a given keyword operation and
returns whichever provider answered, already normalized (schemas.NormalizedKeyword).
Nothing above this module should know or care which provider was used.

ASSUMPTION FLAG: the routing priorities below (Bulk Analysis -> Semrush first,
Suggestions/Questions -> DataForSEO first) are based on typical per-call
pricing for each report type, not measured cost/quota numbers from VTechys's
actual Semrush/DataForSEO accounts. Revisit once real usage/billing data is
available -- treat these as a starting default, not a permanent design.
"""
import time

from . import dataforseo, semrush
from .schemas import NormalizedKeyword

_SEMRUSH_COOLDOWN_UNTIL = 0.0
_SEMRUSH_COOLDOWN_SECONDS = 300

TREND_MIN_GAP_DAYS = 7
TREND_THRESHOLD_PCT = 0.10


def _semrush_available() -> bool:
    return time.monotonic() >= _SEMRUSH_COOLDOWN_UNTIL


def _mark_semrush_cooldown() -> None:
    global _SEMRUSH_COOLDOWN_UNTIL
    _SEMRUSH_COOLDOWN_UNTIL = time.monotonic() + _SEMRUSH_COOLDOWN_SECONDS


def get_keyword_overview(keyword: str) -> NormalizedKeyword:
    """Single-keyword lookup (used when tracking a keyword). Semrush first,
    DataForSEO on error or while Semrush is in a rate-limit cooldown."""
    if _semrush_available():
        row = semrush.fetch_keyword_overview(keyword)
        if row.get("rate_limited"):
            _mark_semrush_cooldown()
        elif not row.get("error"):
            return semrush.normalize_keyword_row(row, keyword)

    row = dataforseo.fetch_keyword_overview(keyword)
    return dataforseo.normalize_keyword_row(row, keyword)


def get_keywords_bulk(keywords: list[str]) -> list[NormalizedKeyword]:
    """
    Bulk Analysis (<=100 keywords). ASSUMPTION (see module docstring): Semrush
    is tried first for all keywords; only keywords it fails to return get a
    DataForSEO fallback call, rather than re-querying the whole list.
    """
    results: dict[str, NormalizedKeyword] = {}

    if _semrush_available():
        rows = semrush.fetch_keywords_bulk(keywords)
        for kw, row in rows.items():
            if row.get("rate_limited"):
                _mark_semrush_cooldown()
            elif not row.get("error"):
                results[kw] = semrush.normalize_keyword_row(row, kw)

    missing = [kw for kw in keywords if kw not in results]
    if missing:
        rows = dataforseo.fetch_keywords_bulk(missing)
        for kw, row in rows.items():
            results[kw] = dataforseo.normalize_keyword_row(row, kw)

    return [results[kw] for kw in keywords if kw in results]


def get_suggestions(seed: str) -> list[NormalizedKeyword]:
    """
    Suggestions & Questions tab. ASSUMPTION (see module docstring): DataForSEO
    is tried first; Semrush's phrase_related/phrase_questions are the fallback
    if DataForSEO returns nothing (missing credentials, no data, error).
    """
    rows = dataforseo.fetch_related_keywords(seed) + dataforseo.fetch_keyword_questions(seed)
    if rows:
        return [dataforseo.normalize_keyword_row(r, r.get("keyword", seed)) for r in rows]

    rows = semrush.fetch_related_keywords(seed) + semrush.fetch_keyword_questions(seed)
    return [semrush.normalize_keyword_row(r, r.get("Ph", seed)) for r in rows]


def compute_trend(snapshots: list) -> tuple[str, str]:
    """
    snapshots: KeywordSnapshot ORM rows for one tracked keyword, any order.

    Rule: compare the latest snapshot against the most recent prior snapshot
    that is at least TREND_MIN_GAP_DAYS old, using volume (falls back to SERP
    position if volume is missing on either side). >10% improvement is
    "rising", >10% decline is "falling", otherwise "stable". Fewer than two
    snapshots, or no snapshot old enough to diff against, means there is
    nothing to compare -- returned as "stable" with confidence
    "insufficient_data" so the frontend can distinguish it from a real "stable".
    """
    ordered = sorted(snapshots, key=lambda s: s.fetched_at, reverse=True)
    if len(ordered) < 2:
        return "stable", "insufficient_data"

    latest = ordered[0]
    baseline = next(
        (s for s in ordered[1:] if (latest.fetched_at - s.fetched_at).days >= TREND_MIN_GAP_DAYS),
        None,
    )
    if baseline is None:
        return "stable", "insufficient_data"

    if latest.volume is not None and baseline.volume:
        change = (latest.volume - baseline.volume) / baseline.volume
    elif latest.position is not None and baseline.position:
        # Lower position number is a better rank, so invert the sign.
        change = (baseline.position - latest.position) / baseline.position
    else:
        return "stable", "insufficient_data"

    if change > TREND_THRESHOLD_PCT:
        return "rising", "computed"
    if change < -TREND_THRESHOLD_PCT:
        return "falling", "computed"
    return "stable", "computed"
