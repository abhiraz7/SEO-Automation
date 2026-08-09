"""
Backlinks provider router (Task 5.1). Routes to Semrush or DataForSEO per
the same global Data Provider switch already used for on-page audits
(routes/settings.py's ProviderSetting, PROVIDERS=("dataforseo","semrush")) --
flip it once in /settings, every tool that reads it (on-page, backlinks)
switches together, per the "switched for all tools" requirement. Keyword
Research is intentionally NOT wired to this switch -- it already has its
own automatic Semrush-primary/DataForSEO-fallback logic (keyword_provider.py),
a different but equally valid way of using both providers, and changing
that would touch a separately-tested, currently-working feature.

Same ok/no_data/error three-outcome contract as keyword_provider's, for the
same reason: a failed call must never be persisted as a fake zero-backlink
snapshot.
"""
from datetime import datetime, timezone

from . import dataforseo, semrush
from .database import SessionLocal
from .schemas import BacklinksOverview


def _active_provider() -> str:
    """Short-lived session, same pattern as settings.py's
    crawler_enabled_flag() -- this module is called from job handlers and
    routes that don't already have a db session threaded through."""
    from .routes.settings import get_active_provider  # local import: avoids a
    # routes -> app-root circular import (settings.py imports this module's
    # sibling dataforseo_onpage, not this file, but keeping the import local
    # here matches the existing crawler_enabled_flag pattern's caution).
    db = SessionLocal()
    try:
        return get_active_provider(db)
    finally:
        db.close()


def get_backlinks_list(base_url: str, limit: int = 100) -> dict:
    """Per-link list for Task 5.2's diffing job. Returns {"rows": [...],
    "error": None}, source chosen by the active Data Provider setting."""
    if _active_provider() == "dataforseo":
        return dataforseo.fetch_backlinks_list(base_url, limit)
    return semrush.fetch_backlinks_list(base_url, limit)


def get_backlinks_overview(base_url: str) -> BacklinksOverview:
    provider = _active_provider()
    fetch = dataforseo.fetch_backlinks_overview if provider == "dataforseo" else semrush.fetch_backlinks_overview
    row = fetch(base_url)

    if row.get("error"):
        return BacklinksOverview(status="error", error=f"{provider}: {row['error']}", fetched_at=datetime.now(timezone.utc))
    if row.get("no_data") or row.get("authority_score") is None:
        return BacklinksOverview(status="no_data", source=provider, fetched_at=datetime.now(timezone.utc))

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
        spam_score=_int(row.get("spam_score")),
        broken_backlinks=_int(row.get("broken_backlinks")),
        tld_distribution=row.get("tld_distribution"),
        platform_distribution=row.get("platform_distribution"),
        source=provider,
        fetched_at=datetime.now(timezone.utc),
    )
