"""
Per-rule wording and measured facts for the On-Page issue screen.

The row tag ("Too long"), the problem headline, the numbers behind it (length,
recommended range, how far over/under) and the "why this matters" line all come
from here. Nothing is invented: numbers are measured from the stored title /
meta description, ranges come from app/audit.py's constants, and the prose is
fixed copy per (category, rule) -- not model output. An AI-written version of
this (specific to the page and site) is Part B of prompts/Audit-Classification-
Task-List.md; this module is its deterministic fallback and the facts layer it
would build on.
"""
import re

from . import audit

# (category, rule) -> short tag shown on the row.
TAGS = {
    ("title", "too_long"): "Too long", ("title", "too_short"): "Too short",
    ("title", "missing"): "Missing", ("title", "duplicate"): "Duplicate",
    ("title", "irrelevant"): "Off-topic",
    ("meta_description", "too_long"): "Too long", ("meta_description", "too_short"): "Too short",
    ("meta_description", "missing"): "Missing", ("meta_description", "duplicate"): "Duplicate",
    ("meta_description", "irrelevant"): "Off-topic",
    ("h1", "missing"): "Missing", ("image_alt", "missing"): "Missing alt",
    ("canonical", "missing"): "Not set", ("opengraph", "missing"): "Not set",
    ("twitter", "missing"): "Not set", ("content", "thin"): "Low text ratio",
    ("security", "no_ssl"): "No HTTPS", ("lang", "missing"): "Missing",
    ("schema", "missing"): "Missing",
}

# (category, rule) -> (headline, detail template, why it matters). The detail
# template may use {length}, {min}, {max}; it is only formatted when those facts
# exist, otherwise the plain issue message is used instead.
COPY = {
    ("title", "too_long"): (
        "Title is longer than recommended",
        "Your title is {length} characters. Long titles may be truncated in search results and can make the page topic less clear.",
        "Search engines may shorten long titles when displaying them. A concise, descriptive title helps both users and search engines understand the page.",
    ),
    ("title", "too_short"): (
        "Title is shorter than recommended",
        "Your title is {length} characters, below the recommended {min}-{max}. Very short titles give search engines little to work with.",
        "A title with the page's main topic gives users and search engines a clearer reason to click and to rank the page.",
    ),
    ("title", "missing"): (
        "Page title is missing",
        "This page has no title tag.",
        "The title is the main headline shown in search results; without it search engines write their own.",
    ),
    ("title", "duplicate"): (
        "Title is duplicated on another page",
        "Another crawled page uses the same title.",
        "Distinct titles help search engines tell pages apart and show the right one for a query.",
    ),
    ("meta_description", "missing"): (
        "Meta description is missing",
        "This page has no meta description.",
        "Search engines can build a snippet from the page text, but a written description lets you control the message shown under your result.",
    ),
    ("meta_description", "too_long"): (
        "Meta description is longer than recommended",
        "Your description is {length} characters. Long descriptions may be cut off in search results.",
        "A description that fits is shown in full, so the whole message reaches the reader.",
    ),
    ("meta_description", "too_short"): (
        "Meta description is shorter than recommended",
        "Your description is {length} characters, below the recommended {min}-{max}.",
        "A fuller description makes better use of the space search engines show under your result.",
    ),
    ("meta_description", "duplicate"): (
        "Meta description is duplicated on another page",
        "Another crawled page uses the same meta tags.",
        "Unique descriptions help each page stand out in search results.",
    ),
    ("h1", "missing"): (
        "No H1 heading found",
        "This page has no H1 tag.",
        "The H1 tells visitors and search engines what the page is about.",
    ),
    ("image_alt", "missing"): (
        "Images are missing alt text",
        "One or more images have no alt text.",
        "Alt text describes an image to screen readers and to search engines that index images.",
    ),
    ("canonical", "missing"): (
        "No canonical link set",
        "This page does not declare a canonical URL.",
        "Optional. Search engines can pick a canonical themselves; declaring one helps when the same content is reachable at several URLs.",
    ),
    ("opengraph", "missing"): (
        "Open Graph tags are missing",
        "No Open Graph title or description was found.",
        "Optional. These control how the page looks when shared on social platforms; they do not affect search ranking.",
    ),
    ("twitter", "missing"): (
        "Twitter card tag is missing",
        "No twitter:card tag was found.",
        "Optional. It controls how the page looks when shared on X/Twitter; it does not affect search ranking.",
    ),
    ("content", "thin"): (
        "Low text-to-page-size ratio",
        "The visible text is a small share of the page's size.",
        "Optional signal. Tool or media-heavy pages can trip it legitimately; it is not a judgment that the content is poor.",
    ),
    ("security", "no_ssl"): (
        "Page is not served over HTTPS",
        "This page loads over plain HTTP.",
        "Browsers warn visitors about non-HTTPS pages, and HTTPS is a basic trust and security signal.",
    ),
    ("lang", "missing"): (
        "Language is not declared",
        "The page has no lang attribute.",
        "The lang attribute helps browsers, screen readers and search engines handle the page in the right language.",
    ),
}


def rule_tag(category: str, rule: str) -> str:
    """Short row tag; unknown pairs fall back to the rule name, humanised."""
    return TAGS.get((category, rule)) or (rule or "").replace("_", " ").capitalize()


def _stored_text(page, category):
    value = {"title": getattr(page, "title", None), "meta_description": getattr(page, "meta_description", None)}.get(category)
    return (value or "").strip()


def facts(page, category: str) -> dict | None:
    """Measured facts for the categories that have a real length: the current
    length, the recommended range, and how far over/under it is. None for every
    other category, or when there is nothing stored to measure."""
    ranges = {
        "title": (audit.TITLE_MIN, audit.TITLE_MAX),
        "meta_description": (audit.META_DESC_MIN, audit.META_DESC_MAX),
    }
    if category not in ranges or page is None:
        return None
    text = _stored_text(page, category)
    if not text:
        return None
    lo, hi = ranges[category]
    n = len(text)
    return {"length": n, "min": lo, "max": hi, "over_by": max(0, n - hi), "under_by": max(0, lo - n)}


_STOP = {"a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "with", "at", "by", "is", "are", "your"}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[\w']+", (text or "").casefold()) if t not in _STOP}


def _norm(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def check_suggestion(category: str, suggested: str, page=None) -> list[dict]:
    """Deterministic checks on a suggested replacement, computed in code (never
    by the model) so a green tick always means "we verified this". Each check is
    {label, passed, hard}. A failed hard check means the suggestion isn't ready
    to use; a failed soft check is a caution. Length checks only apply to the
    categories that have a real range (title / meta description); nothing here
    judges relevance or intent -- that stays an unverified AI claim in the UI."""
    text = (suggested or "").strip()
    current = _stored_text(page, category)
    out = [{"label": "Suggestion is not empty", "passed": bool(text), "hard": True}]
    if not text:
        return out
    ranges = {"title": (audit.TITLE_MIN, audit.TITLE_MAX), "meta_description": (audit.META_DESC_MIN, audit.META_DESC_MAX)}
    if category in ranges:
        lo, hi = ranges[category]
        n = len(text)
        out.append({"label": f"Length is {n} characters (guideline {lo}-{hi})", "passed": lo <= n <= hi, "hard": True})
        if current:
            out.append({"label": "Differs from the current text", "passed": _norm(text) != _norm(current), "hard": False})
            cur_tokens = _tokens(current)
            if cur_tokens:
                kept = len(_tokens(text) & cur_tokens) / len(cur_tokens)
                out.append({"label": "Keeps key terms from the current text", "passed": kept >= 0.3, "hard": False})
            source = " ".join([current, getattr(page, "title", None) or "", getattr(page, "meta_description", None) or "",
                               " ".join(getattr(page, "h1", None) or [])])
            new_numbers = set(re.findall(r"\d+", text)) - set(re.findall(r"\d+", source))
            out.append({"label": "Adds no new numbers or years", "passed": not new_numbers, "hard": False})
    return out


def checks_summary(checks: list[dict]) -> dict:
    """{state, passed, total}: state is 'review' if any hard check failed,
    'partial' if only soft checks failed, else 'ok'."""
    passed = sum(1 for c in checks if c["passed"])
    if any(not c["passed"] and c["hard"] for c in checks):
        state = "review"
    elif passed < len(checks):
        state = "partial"
    else:
        state = "ok"
    return {"state": state, "passed": passed, "total": len(checks)}


def explain(category: str, rule: str, message: str, page=None) -> dict:
    """{tag, headline, detail, why, facts} for the issue screen. Falls back to
    the stored issue message when there is no fixed copy for the rule, or when
    the copy needs a measurement that isn't available."""
    f = facts(page, category)
    entry = COPY.get((category, rule))
    if entry is None:
        return {"tag": rule_tag(category, rule), "headline": message, "detail": "", "why": "", "facts": f}
    headline, detail, why = entry
    if "{" in detail:
        detail = detail.format(**f) if f else message
    return {"tag": rule_tag(category, rule), "headline": headline, "detail": detail, "why": why, "facts": f}
