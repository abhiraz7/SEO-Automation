"""
Competitor Analysis data flow (Phase 4):

    Competitor records -> provider adapters -> normalized dict -> this
    module -> UI

No provider-specific response shape (Semrush CSV columns, DataForSEO's
nested result/task structure) ever reaches a template -- callers of this
module only ever see the plain dict shape returned by fetch_site_metrics
below. No new provider integration was written for this module; it reuses
semrush.fetch_domain_metrics (organic keywords -- Semrush-only today, no
DataForSEO domain-overview equivalent exists in this codebase yet) and
backlinks_provider.get_backlinks_overview (referring domains + total
backlinks -- already dual-provider, follows the active /settings Data
Provider switch).

Deliberately no AI here -- see CompetitorSnapshot's docstring. This module
only fetches and normalizes facts; comparison/scoring stays in the route
layer for now (Phase 3 keeps that simple: a plain table, no computed
"opportunity" yet), and any future AI interpretation layer consumes
CompetitorSnapshot rows, not live provider calls.
"""
from datetime import datetime, timezone

from .. import backlinks_provider, models, semrush


def fetch_site_metrics(base_url: str) -> dict:
    """One domain's comparison-table row: organic_keywords, referring_domains,
    total_backlinks, plus which provider each came from and any error. Never
    raises -- a failed provider call shows up as error text, not a crash or
    a silently fabricated zero (same discipline as every other provider
    integration in this codebase)."""
    result = {
        "organic_keywords": None,
        "referring_domains": None,
        "total_backlinks": None,
        "keywords_source": None,
        "backlinks_source": None,
        "error": None,
    }
    errors = []

    domain_metrics = semrush.fetch_domain_metrics(base_url)
    if domain_metrics.get("error"):
        # fetch_domain_metrics makes two internal Semrush calls (domain_ranks
        # for keywords, its OWN separate backlinks_overview for authority
        # score -- unrelated to backlinks_provider's call below) and bundles
        # both errors into one string. Label it clearly as Semrush-specific
        # so a failure here never reads as "the backlinks data is wrong" --
        # referring_domains/total_backlinks come from a different call below.
        errors.append(f"organic keywords (semrush domain lookup): {domain_metrics['error']}")
    else:
        try:
            result["organic_keywords"] = int(float(domain_metrics.get("organic_keywords") or 0)) or None
        except (TypeError, ValueError):
            pass
        result["keywords_source"] = "semrush"

    backlinks = backlinks_provider.get_backlinks_overview(base_url)
    if backlinks.status == "error":
        errors.append(f"backlinks: {backlinks.error}")
    elif backlinks.status == "ok":
        result["referring_domains"] = backlinks.referring_domains
        result["total_backlinks"] = backlinks.total_backlinks
        result["backlinks_source"] = backlinks.source

    if errors:
        result["error"] = " | ".join(errors)
    return result


def refresh_project_comparison(db, project: "models.Project", competitors: list) -> list:
    """Fetches fresh metrics for the project's own site + every active
    competitor, writes one CompetitorSnapshot row each (never overwrites --
    same point-in-time history pattern as BacklinkSnapshot/KeywordSnapshot),
    returns the newly-written rows. Caller (the route) decides when this
    runs -- never on a plain page load, only an explicit Refresh action."""
    written = []

    self_metrics = fetch_site_metrics(project.base_url)
    self_snapshot = models.CompetitorSnapshot(
        project_id=project.id,
        competitor_id=None,
        organic_keywords=self_metrics["organic_keywords"],
        referring_domains=self_metrics["referring_domains"],
        total_backlinks=self_metrics["total_backlinks"],
        keywords_source=self_metrics["keywords_source"],
        backlinks_source=self_metrics["backlinks_source"],
        error=self_metrics["error"],
        fetched_at=datetime.now(timezone.utc),
    )
    db.add(self_snapshot)
    written.append(self_snapshot)

    for competitor in competitors:
        metrics = fetch_site_metrics(f"https://{competitor.domain}")
        snapshot = models.CompetitorSnapshot(
            project_id=project.id,
            competitor_id=competitor.id,
            organic_keywords=metrics["organic_keywords"],
            referring_domains=metrics["referring_domains"],
            total_backlinks=metrics["total_backlinks"],
            keywords_source=metrics["keywords_source"],
            backlinks_source=metrics["backlinks_source"],
            error=metrics["error"],
            fetched_at=datetime.now(timezone.utc),
        )
        db.add(snapshot)
        written.append(snapshot)

    db.commit()
    for row in written:
        db.refresh(row)
    return written


def latest_comparison(db, project_id: int, competitors: list) -> dict:
    """Reads the most recent snapshot per row (project's own site + each
    competitor) without calling any provider -- what the page loads on a
    normal visit. None if never refreshed yet."""
    def _latest(competitor_id):
        return (
            db.query(models.CompetitorSnapshot)
            .filter(
                models.CompetitorSnapshot.project_id == project_id,
                models.CompetitorSnapshot.competitor_id == competitor_id,
            )
            .order_by(models.CompetitorSnapshot.fetched_at.desc())
            .first()
        )

    return {
        "self": _latest(None),
        "competitors": [(c, _latest(c.id)) for c in competitors],
    }
