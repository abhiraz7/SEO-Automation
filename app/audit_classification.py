"""
Central classification registry for Issue rows.

Every provider adapter that produces on-page findings (dataforseo_onpage.py,
audit.py -- and any future ones) emits (category, rule) pairs like
("canonical", "missing") or ("title", "too_short"). This module is the one
place that decides what those pairs MEAN: how much they should matter
(impact) and whether they should be allowed to move the technical health
score at all (score_eligible).

Materialized, not looked-up live: classify() is called once, at Issue
creation time, in each provider's _issue() helper -- its output is stamped
onto the Issue row (migration 028) rather than re-derived on every read.
That distinction matters for historical accuracy: if a future audit
correction moves ("canonical", "missing") to a different impact, old Issue
rows keep whatever classification_version they were created under instead
of silently being reinterpreted. CLASSIFICATION_VERSION is bumped whenever
RULES changes in a way that would alter stored classifications.

See the CLASSIFICATION_VERSION changelog comment below for what each
version actually changed and when.
"""

from dataclasses import dataclass

CLASSIFICATION_VERSION = 2
# v2 (2026-09-29): downgraded canonical/opengraph/twitter/meta_description
# "missing" and content/thin from score-eligible defects to informational
# opportunities. Google doesn't require any of these on every page (a
# missing canonical, OG tag, Twitter card, or meta description is a normal,
# valid state, not a technical fault), and low_content_rate is a text/page
# ratio, not a judgment that the page's content is bad -- see the audit
# review discussion 2026-09-29. Rows created under an earlier
# classification_version keep whatever interpretation was in force when
# they were written; only audits run after this change get the new one --
# that's the whole point of stamping the version rather than deriving
# classification live at read time.


@dataclass(frozen=True)
class RuleClassification:
    impact: str  # high | medium | low
    score_eligible: bool


# Default for any (category, rule) pair a provider emits that isn't listed
# below yet -- keeps classify() total instead of raising on an unmapped rule.
_DEFAULT = RuleClassification(impact="medium", score_eligible=True)

RULES: dict[tuple[str, str], RuleClassification] = {
    ("title", "missing"): RuleClassification("high", True),
    ("title", "too_short"): RuleClassification("medium", True),
    ("title", "too_long"): RuleClassification("medium", True),
    ("title", "duplicate"): RuleClassification("medium", True),
    ("title", "irrelevant"): RuleClassification("medium", True),

    # Google can generate a snippet from page content when no meta
    # description is present -- it's an optimization opportunity, not a
    # technical fault, so it no longer moves the health score.
    ("meta_description", "missing"): RuleClassification("medium", False),
    ("meta_description", "too_short"): RuleClassification("medium", True),
    ("meta_description", "too_long"): RuleClassification("medium", True),
    ("meta_description", "duplicate"): RuleClassification("medium", True),
    ("meta_description", "irrelevant"): RuleClassification("medium", True),

    ("h1", "missing"): RuleClassification("high", True),
    ("h1", "multiple"): RuleClassification("medium", True),
    ("h1", "too_short"): RuleClassification("medium", True),
    ("h1", "too_long"): RuleClassification("medium", True),

    ("h2", "missing"): RuleClassification("medium", True),
    ("h2", "poor_structure"): RuleClassification("medium", True),

    ("image_alt", "missing"): RuleClassification("medium", True),
    ("image_alt", "empty"): RuleClassification("medium", True),

    ("schema", "missing"): RuleClassification("high", True),
    ("schema", "invalid"): RuleClassification("medium", True),

    # Google can select its own canonical and explicitly says a page doesn't
    # need one declared -- absence alone isn't a defect (a broken/looping/
    # redirect-target canonical would be, but this app doesn't detect those
    # yet; see the audit review discussion 2026-09-29).
    ("canonical", "missing"): RuleClassification("low", False),

    # Open Graph / Twitter Card are social-sharing presentation metadata,
    # not a Google Search or technical-health signal.
    ("opengraph", "missing"): RuleClassification("low", False),
    ("twitter", "missing"): RuleClassification("low", False),

    ("lang", "missing"): RuleClassification("high", True),

    # low_content_rate is a plaintext/page-size ratio, not a content-quality
    # judgment -- a calculator, tool, or media-heavy page can trip this
    # legitimately. Surfaced as an opportunity, doesn't move the score.
    ("content", "thin"): RuleClassification("low", False),

    ("security", "no_ssl"): RuleClassification("high", True),
    ("security", "ssl_error"): RuleClassification("high", True),
    ("security", "missing_headers"): RuleClassification("medium", True),
    ("security", "robots_missing"): RuleClassification("medium", True),
}


def classify(category: str, rule: str) -> dict:
    """(category, rule) -> {"impact", "score_eligible", "classification_version"},
    ready to merge into an issue dict before it's stamped onto an Issue row."""
    c = RULES.get((category, rule), _DEFAULT)
    return {
        "impact": c.impact,
        "score_eligible": c.score_eligible,
        "classification_version": CLASSIFICATION_VERSION,
    }


# ── Health scoring ───────────────────────────────────────────────────────
# The site-wide "Site Health" % (project cards, onpage dashboard) and the
# legacy per-page audit.page_score() both used to sum EVERY stored issue,
# so 20 pages missing a Twitter card could singlehandedly tank a site's
# score. These two helpers are now the only places that turn issues into a
# number -- both skip anything with score_eligible=False (informational
# findings still show up in the issue list/CSV/suggestions, they just don't
# move the score). Consolidating here also kills the third, differently-
# weighted formula that used to live duplicated in routes/projects.py and
# templates/onpage_semrush.html.

def score_eligible_severity_counts(issues) -> tuple[int, int]:
    """issues (ORM Issue rows, or dicts shaped like _issue()'s output) ->
    (error_count, warning_count), counting only score_eligible ones."""
    errors = warnings = 0
    for issue in issues:
        if isinstance(issue, dict):
            eligible = issue.get("score_eligible", True)
            severity = issue.get("severity")
        else:
            eligible = issue.score_eligible
            severity = issue.severity
        if eligible is False:
            continue
        if severity == "error":
            errors += 1
        else:
            warnings += 1
    return errors, warnings


def health_from_counts(error_count: int, warning_count: int) -> int:
    """(error_count, warning_count) -- already filtered to score_eligible --
    -> a 0-100 site health score. Weighting (error -4, warning -1, warnings
    capped at -40) is unchanged from the pre-refactor formula; only which
    issues get counted changed."""
    return max(0, 100 - error_count * 4 - min(warning_count, 40))


def project_health_score(issues) -> int:
    """issues (ORM Issue rows or _issue()-shaped dicts) -> 0-100 site health,
    in one step. Equivalent to health_from_counts(*score_eligible_severity_counts(issues))."""
    errors, warnings = score_eligible_severity_counts(issues)
    return health_from_counts(errors, warnings)
