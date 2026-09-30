"""
AI Visibility Score -- our own formula computed over VisibilityCheck rows
already collected by routes/visibility.py (Google AI Overview citation
data, via the SAME /serp/google/organic/live/advanced call rank tracking
already makes -- zero extra API cost).

This is NOT a number Google or DataForSEO reports -- it is a percentage WE
define, over data they returned. Naming and display must always make that
clear (same "post-change performance, not causal attribution" honesty rule
the GSC feature's design doc applies to Search Console data).

Formula: of the checks where an AI Overview actually appeared for the
query, what percentage cited this project's own domain. Checks where no AI
Overview appeared at all are excluded from the denominator -- including
them would penalize a project for a query Google chose not to show an AI
Overview for, which has nothing to do with the project's own visibility.
"""
from dataclasses import dataclass

from .. import models


@dataclass
class AIVisibilityScore:
    score: float | None  # 0-100, or None when no_data (no AI Overview ever seen)
    checks_with_ai_overview: int
    checks_cited: int
    total_checks: int
    avg_organic_rank: float | None

    @property
    def has_data(self) -> bool:
        return self.checks_with_ai_overview > 0


def compute_ai_visibility_score(checks: list[models.VisibilityCheck]) -> AIVisibilityScore:
    usable = [c for c in checks if not c.raw_error]
    with_ai_overview = [c for c in usable if c.ai_overview_present]
    cited = [c for c in with_ai_overview if c.brand_in_ai_overview]

    score = (len(cited) / len(with_ai_overview) * 100) if with_ai_overview else None

    ranks = [c.organic_rank for c in usable if c.organic_rank is not None]
    avg_rank = (sum(ranks) / len(ranks)) if ranks else None

    return AIVisibilityScore(
        score=score,
        checks_with_ai_overview=len(with_ai_overview),
        checks_cited=len(cited),
        total_checks=len(usable),
        avg_organic_rank=avg_rank,
    )
