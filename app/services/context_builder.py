"""
Builds a structured, LLM-derived understanding of a single page: its type, topic,
search intent, target keyword(s), and which part of the business profile (service /
location / audience) the page's own content is actually relevant to.

One Claude call per page (temperature=0 for deterministic, repeatable output;
JSON-only response; retried once if the response isn't valid JSON). Results are
cached per crawl snapshot in the page_understanding table — re-analyzing a page
whose content hasn't changed since the last crawl costs nothing.
"""
import json

from pydantic import ValidationError
from sqlalchemy.orm import Session

from .. import claude as claude_client
from .. import models
from ..schemas import PageUnderstandingResult

MODEL = "claude-haiku-4-5-20251001"
TEMPERATURE = 0
MAX_TOKENS = 1024

# No tokenizer dependency in this project; ~4 characters/token is a standard,
# conservative approximation for English text.
CHARS_PER_TOKEN = 4
FIT_MARKDOWN_TOKEN_LIMIT = 3000
FIT_MARKDOWN_CHAR_LIMIT = FIT_MARKDOWN_TOKEN_LIMIT * CHARS_PER_TOKEN


def _truncate_fit_markdown(text: str) -> str:
    return (text or "")[:FIT_MARKDOWN_CHAR_LIMIT]


def _profile_block(profile: models.BusinessProfile | None) -> str:
    if profile is None:
        return "(no business profile set for this project)"
    return (
        f"Brand: {profile.brand or 'N/A'}\n"
        f"Industry: {profile.industry or 'N/A'}\n"
        f"Services: {', '.join(profile.services or []) or 'N/A'}\n"
        f"Locations: {', '.join(profile.locations or []) or 'N/A'}\n"
        f"Audiences: {', '.join(profile.audiences or []) or 'N/A'}\n"
        f"Tone: {profile.tone or 'N/A'}\n"
        f"USP: {profile.usp or 'N/A'}"
    )


def _build_prompt(page: models.Page, profile: models.BusinessProfile | None, fit_markdown: str) -> str:
    return f"""You are an SEO content analyst. Analyze this page and return ONLY a single JSON object — no prose, no markdown code fences, no explanation before or after it.

Page URL: {page.url}
Page title: {page.title or 'N/A'}
Meta description: {page.meta_description or 'N/A'}

Business profile:
{_profile_block(profile)}

Page content (fit markdown, truncated to ~{FIT_MARKDOWN_TOKEN_LIMIT} tokens):
{fit_markdown}

Return a JSON object with exactly these keys:
- "page_type": string (e.g. "blog post", "product page", "service page", "landing page", "category page", "homepage")
- "main_topic": string, the page's core subject in a few words
- "search_intent": string, one of "informational", "commercial", "transactional", "navigational"
- "primary_keyword": string, the single best-fit target keyword for this page
- "secondary_keywords": array of 3-5 supporting keyword strings
- "relevant_service": string or null — the single service from the business profile's Services list this page is actually about, or null if none clearly apply
- "relevant_location": string or null — the single location from the business profile's Locations list this page is actually about
- "relevant_audience": string or null — the single audience from the business profile's Audiences list this page is actually written for
- "geo_relevance": string, one of "global", "regional", "local" — how geographically specific the PAGE CONTENT itself is
- "context_confidence": number between 0 and 1, your confidence in this analysis

CRITICAL RULE: set "relevant_location" to null unless the PAGE CONTENT ITSELF is geo-specific (e.g. it explicitly names a city/region/service area, or is clearly written for a local audience). Do not set it just because the business profile has a location on file — the page's own content must justify it."""


def _parse_response(raw: str) -> PageUnderstandingResult:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    data = json.loads(text)
    return PageUnderstandingResult(**data)


def _latest_snapshot(db: Session, page_id: int) -> models.CrawlSnapshot | None:
    return (
        db.query(models.CrawlSnapshot)
        .filter(models.CrawlSnapshot.page_id == page_id)
        .order_by(models.CrawlSnapshot.crawled_at.desc())
        .first()
    )


def _cached_row(db: Session, page_id: int, snapshot_id: int) -> models.PageUnderstanding | None:
    return (
        db.query(models.PageUnderstanding)
        .filter(
            models.PageUnderstanding.page_id == page_id,
            models.PageUnderstanding.snapshot_id == snapshot_id,
        )
        .first()
    )


def build_page_understanding(db: Session, page: models.Page) -> models.PageUnderstanding:
    """Return the cached page_understanding row for page's latest crawl snapshot,
    generating one with a single Claude call (retried once on malformed JSON) if
    no cached row exists yet for that snapshot."""
    snapshot = _latest_snapshot(db, page.id)
    if snapshot is None:
        raise ValueError(f"Page {page.id} has no crawl snapshot to analyze yet")

    cached = _cached_row(db, page.id, snapshot.id)
    if cached is not None:
        return cached

    profile = (
        db.query(models.BusinessProfile)
        .filter(models.BusinessProfile.project_id == page.project_id)
        .first()
    )
    fit_markdown = _truncate_fit_markdown(page.fit_markdown or page.custom_content)
    prompt = _build_prompt(page, profile, fit_markdown)

    try:
        raw = claude_client.complete(prompt, max_tokens=MAX_TOKENS, temperature=TEMPERATURE, model=MODEL)
        result = _parse_response(raw)
    except (json.JSONDecodeError, ValidationError):
        # Retry once — LLMs occasionally wrap JSON in prose despite instructions.
        raw = claude_client.complete(prompt, max_tokens=MAX_TOKENS, temperature=TEMPERATURE, model=MODEL)
        result = _parse_response(raw)

    row = models.PageUnderstanding(
        page_id=page.id,
        snapshot_id=snapshot.id,
        understanding_json=result.model_dump(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
