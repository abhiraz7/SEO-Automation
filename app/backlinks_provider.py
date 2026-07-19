"""
Backlinks provider router (Task 5.1). Semrush only for now -- DataForSEO has
a comparable backlinks_overview endpoint but wiring it is out of scope here
(same "assumption flag" pattern as keyword_provider.py: revisit once there's
a reason to add a second source). Same ok/no_data/error three-outcome
contract as keyword_provider.get_keyword_overview, for the same reason: a
failed Semrush call must never be persisted as a fake zero-backlink snapshot.
"""
from datetime import datetime, timezone

from . import semrush
from .schemas import BacklinksOverview


def get_backlinks_overview(base_url: str) -> BacklinksOverview:
    row = semrush.fetch_backlinks_overview(base_url)

    if row.get("error"):
        return BacklinksOverview(status="error", error=f"semrush: {row['error']}", fetched_at=datetime.now(timezone.utc))
    if row.get("no_data") or row.get("authority_score") is None:
        return BacklinksOverview(status="no_data", fetched_at=datetime.now(timezone.utc))

    def _int(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    return BacklinksOverview(
        status="ok",
        authority_score=_int(row.get("authority_score")),
        referring_domains=_int(row.get("referring_domains")),
        total_backlinks=_int(row.get("total_backlinks")),
        follow_links=_int(row.get("follow_links")),
        nofollow_links=_int(row.get("nofollow_links")),
        source="semrush",
        fetched_at=datetime.now(timezone.utc),
    )
