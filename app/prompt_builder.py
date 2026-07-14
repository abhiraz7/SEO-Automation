"""
Single home for ALL AI prompt construction.

Every AI feature (meta title, meta description, H1/H2, schema, AI visibility, ...)
must build its context and prompt here — no inline prompt strings in claude.py,
routes, or templates. claude.py is a thin API client that sends what this module
builds.

Context pipeline:

    Business Profile  +  Crawl4AI Fit Markdown  +  Current SEO Metadata
        +  Current Audit Issue  +  (future: SEMrush / DataForSEO data)
                                ↓
                          build_context()
                                ↓
                 build_suggestion_prompt() / build_meta_optimization_prompt()
                                ↓
                             Claude
"""
from . import audit

FIT_MARKDOWN_LIMIT = 3000

PROFILE_FIELDS = [
    ("Business name", "business_name"),
    ("Business description", "business_description"),
    ("Industry", "industry"),
    ("Products / services", "products_services"),
    ("Target audience", "target_audience"),
    ("Primary market", "primary_market"),
    ("Brand tone", "brand_tone"),
]


def _flatten_current_value(current: dict) -> str:
    """Collapse audit.current_value_for()'s structured payload into a plain string."""
    kind = current["kind"]
    if kind == "text":
        return current["value"] or "N/A"
    if kind == "list":
        return ", ".join(current["items"]) if current["items"] else "N/A"
    if kind == "kv":
        pairs = [f"{label}: {value}" for label, value in current["items"] if value]
        return "; ".join(pairs) if pairs else "N/A"
    if kind == "images":
        return f"{len(current['items'])} image(s) on page" if current["items"] else "N/A"
    if kind == "schema":
        return ", ".join(current["types"]) if current["types"] else "N/A"
    if kind == "markdown":
        return current["excerpt"] or "N/A"
    return "N/A"


def build_context(page, issue=None, business_profile=None, semrush_data=None) -> dict:
    """Assemble everything the AI needs to know about a page into one dict.

    semrush_data is a placeholder for future keyword/competitor enrichment —
    accepting it now means callers won't need signature changes later.
    """
    context = {
        "url": page.url,
        "title": page.title,
        "meta_description": page.meta_description,
        "fit_markdown": (page.fit_markdown or page.custom_content or "")[:FIT_MARKDOWN_LIMIT],
        "issue_category": issue.category if issue else None,
        "issue_message": issue.message if issue else None,
        "current_value": _flatten_current_value(audit.current_value_for(page, issue.category)) if issue else None,
        "business_profile": business_profile,
        "semrush_data": semrush_data,
    }
    return context


def _profile_block(profile) -> str:
    """Render the business-profile section of a prompt. Empty string when no profile."""
    if profile is None:
        return ""
    lines = [f"- {label}: {getattr(profile, attr)}" for label, attr in PROFILE_FIELDS if getattr(profile, attr, None)]

    location_parts = [p for p in (profile.city, profile.state_region, profile.country) if p]
    geo = ""
    if location_parts:
        location = ", ".join(location_parts)
        lines.append(f"- Location: {location}")
        geo = (
            f"\nTarget location: {location}. Write for that local market — use locally "
            "relevant language patterns, spellings, currency, place names and cultural "
            "references. Do not produce generic copy that could apply to any city."
        )

    if not lines:
        return ""
    return "\nBusiness context:\n" + "\n".join(lines) + geo + "\n"


SUGGESTION_COUNT = 3
GEO_RELEVANCE_HIGH = "local"  # threshold at which a suggestion prompt includes location at all


def resolve_profile_slice(business_profile, understanding: dict | None) -> dict:
    """Narrow the full business profile down to just what PageUnderstanding resolved
    as actually relevant to THIS page, instead of dumping every profile field into
    the prompt. Location is included only when the page's own content is highly
    geo-specific (geo_relevance == 'local') — mirrors the same rule context_builder
    uses to decide relevant_location in the first place, applied a second time here
    since a page can be reused across profile edits after its understanding was cached."""
    understanding = understanding or {}
    include_location = understanding.get("geo_relevance") == GEO_RELEVANCE_HIGH
    return {
        "brand": getattr(business_profile, "brand", None) if business_profile else None,
        "relevant_service": understanding.get("relevant_service"),
        "relevant_location": understanding.get("relevant_location") if include_location else None,
        "relevant_audience": understanding.get("relevant_audience"),
        "tone": getattr(business_profile, "tone", None) if business_profile else None,
    }


def build_suggestion_context(page, issue, business_profile=None, understanding: dict = None) -> dict:
    """Context for the suggestion-generation prompt. Unlike build_context(), this does
    NOT send raw fit_markdown — PageUnderstanding's distilled JSON (topic, intent,
    keyword) stands in for it, so regenerating suggestions doesn't re-send the full
    page content (and its token cost) every time."""
    return {
        "url": page.url,
        "issue_category": issue.category,
        "issue_message": issue.message,
        "current_value": _flatten_current_value(audit.current_value_for(page, issue.category)),
        "understanding": understanding or {},
        "profile_slice": resolve_profile_slice(business_profile, understanding),
    }


def build_suggestion_prompt(context: dict) -> str:
    """Prompt for the suggestions-per-issue feature. Built from build_suggestion_context()'s
    understanding + resolved profile slice + issue — deliberately not the full profile
    or raw fit_markdown. Output format (numbered list 1-N) is unchanged so downstream
    parsing (claude.generate_suggestions) and Rule Validation are unaffected."""
    understanding = context.get("understanding") or {}
    slice_ = context.get("profile_slice") or {}

    profile_lines = [
        f"- {label}: {value}"
        for label, value in [
            ("Brand", slice_.get("brand")),
            ("Relevant service", slice_.get("relevant_service")),
            ("Relevant location", slice_.get("relevant_location")),
            ("Relevant audience", slice_.get("relevant_audience")),
            ("Brand tone", slice_.get("tone")),
        ]
        if value
    ]
    profile_block = ("\nBusiness context (resolved for this page):\n" + "\n".join(profile_lines) + "\n") if profile_lines else ""

    understanding_lines = [
        f"- {label}: {value}"
        for label, value in [
            ("Page type", understanding.get("page_type")),
            ("Main topic", understanding.get("main_topic")),
            ("Search intent", understanding.get("search_intent")),
            ("Primary keyword", understanding.get("primary_keyword")),
            ("Secondary keywords", ", ".join(understanding.get("secondary_keywords") or []) or None),
        ]
        if value
    ]
    understanding_block = ("\nPage understanding:\n" + "\n".join(understanding_lines) + "\n") if understanding_lines else ""

    return f"""You are an SEO expert. Generate exactly {SUGGESTION_COUNT} distinct suggestions to fix this SEO issue.

Issue category: {context.get('issue_category')}
Issue: {context.get('issue_message')}
Page URL: {context.get('url', '')}
Current value: {context.get('current_value', 'N/A')}
{profile_block}{understanding_block}
Return ONLY a numbered list 1-{SUGGESTION_COUNT}. Each suggestion is a ready-to-use replacement value, not advice."""


def build_meta_optimization_prompt(context: dict) -> str:
    """Prompt for optimizing a page's meta title + description in one shot."""
    return f"""You are an SEO expert. Generate an optimized meta title and meta description for this page.

Page URL: {context.get('url', '')}
Current title: {context.get('title') or 'N/A'}
Current meta description: {context.get('meta_description') or 'N/A'}
{_profile_block(context.get('business_profile'))}
Page content (fit markdown):
{context.get('fit_markdown', '')}

Return ONLY these two lines:
Title: <optimized title, 30-60 characters>
Description: <optimized meta description, 50-160 characters>"""
