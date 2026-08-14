"""
DataForSEO On-Page API adapter -- the sole source of on-page SEO data in the
lite app. No crawler of our own anywhere: instant_pages does a synchronous
single-URL check, task_post/tasks_ready/pages run a whole-site crawl on
DataForSEO's end that we just poll and pull results from. Both bill at the
same $0.00015/page BASIC rate (confirmed 2026-08-03), so the split here is
purely about which UX fits -- one URL right now vs. a whole site over time.

Auth: HTTP Basic (DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD), same as the
parent app's app/dataforseo.py.
"""
import os

import httpx

DATAFORSEO_BASE = "https://api.dataforseo.com/v3"
_TIMEOUT = 30.0


def _auth() -> tuple[str, str] | None:
    login = os.environ.get("DATAFORSEO_LOGIN", "").strip()
    password = os.environ.get("DATAFORSEO_PASSWORD", "").strip()
    if not login or not password:
        return None
    return (login, password)


def is_configured() -> bool:
    return _auth() is not None


def _post(path: str, payload: list[dict]) -> dict:
    auth = _auth()
    if not auth:
        return {"error": "No DataForSEO credentials (DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD not set)"}
    try:
        resp = httpx.post(f"{DATAFORSEO_BASE}{path}", json=payload, auth=auth, timeout=_TIMEOUT)
        data = resp.json()
    except Exception as e:
        return {"error": str(e)}
    if data.get("status_code") != 20000:
        return {"error": data.get("status_message") or f"HTTP {resp.status_code}"}
    return data


def _get(path: str) -> dict:
    auth = _auth()
    if not auth:
        return {"error": "No DataForSEO credentials (DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD not set)"}
    try:
        resp = httpx.get(f"{DATAFORSEO_BASE}{path}", auth=auth, timeout=_TIMEOUT)
        data = resp.json()
    except Exception as e:
        return {"error": str(e)}
    if data.get("status_code") != 20000:
        return {"error": data.get("status_message") or f"HTTP {resp.status_code}"}
    return data


# ── Instant Pages (synchronous, single URL) ─────────────────────────────

def instant_page_check(url: str) -> dict:
    """Live single-URL on-page check. Returns the raw result item, or
    {"error": ...} if the call failed. Never {"no_data": True} -- a live
    fetch of a real URL either returns page data or fails outright."""
    data = _post("/on_page/instant_pages", [{"url": url, "enable_javascript": False}])
    if data.get("error"):
        return data
    try:
        result = data["tasks"][0]["result"][0]
        items = result.get("items") or []
        if not items:
            return {"error": "DataForSEO returned no page data for this URL."}
        return items[0]
    except (KeyError, IndexError):
        return {"error": "Unexpected response shape from DataForSEO instant_pages."}


# ── Task-based site-wide crawl (async, DataForSEO's own crawler) ────────

def start_site_task(target: str, max_crawl_pages: int = 20) -> dict:
    """Posts a whole-site crawl task. target is a bare domain, no scheme.
    Returns {"task_id": ...} or {"error": ...}."""
    domain = target.strip().replace("https://", "").replace("http://", "").rstrip("/")
    data = _post("/on_page/task_post", [{
        "target": domain,
        "max_crawl_pages": max_crawl_pages,
        "enable_javascript": False,
    }])
    if data.get("error"):
        return data
    try:
        task = data["tasks"][0]
        if task.get("status_code") not in (20100, 20000):
            return {"error": task.get("status_message") or "Task creation failed"}
        return {"task_id": task["id"]}
    except (KeyError, IndexError):
        return {"error": "Unexpected response shape from DataForSEO task_post."}


def is_task_ready(task_id: str) -> bool:
    """Checks the tasks_ready list for this specific task_id. DataForSEO's
    tasks_ready returns ALL ready tasks account-wide, so this filters to
    the one we care about rather than assuming order."""
    data = _get("/on_page/tasks_ready")
    if data.get("error"):
        return False
    try:
        result = data["tasks"][0].get("result") or []
    except (KeyError, IndexError):
        return False
    return any(item.get("id") == task_id for item in result)


def fetch_task_pages(task_id: str, limit: int = 100) -> dict:
    """Pulls crawled page results for a ready task. Returns
    {"pages": [...], "pages_crawled": N} or {"error": ...}."""
    data = _post("/on_page/pages", [{"id": task_id, "limit": limit}])
    if data.get("error"):
        return data
    try:
        result = data["tasks"][0]["result"][0]
        return {"pages": result.get("items") or [], "pages_crawled": result.get("crawl_progress") and result.get("total_items_count")}
    except (KeyError, IndexError):
        return {"error": "Unexpected response shape from DataForSEO on_page/pages."}


def fetch_task_links(task_id: str, limit: int = 1000) -> dict:
    """Pulls every link (anchor/image/canonical/redirect/...) DataForSEO found
    while crawling this task -- the Link Analyzer's sole data source. Same
    task_id as fetch_task_pages, no separate crawl/cost. Returns
    {"links": [...]} or {"error": ...}. DataForSEO caps a single page at 1000
    results; a site with more links than that gets a partial (but still
    representative -- broken links surface early) set rather than us adding
    pagination for a v1."""
    data = _post("/on_page/links", [{"id": task_id, "limit": limit}])
    if data.get("error"):
        return data
    try:
        result = data["tasks"][0]["result"][0]
        return {"links": result.get("items") or []}
    except (KeyError, IndexError):
        return {"error": "Unexpected response shape from DataForSEO on_page/links."}


# ── checks -> our Issue vocabulary ───────────────────────────────────────
# DataForSEO's `checks` object is ~50 booleans (true = problem present, for
# most of them). This maps a curated subset onto the same category/rule/
# severity/message shape the parent app's audit.py produces, so the rest of
# the app (suggestion generation, WordPress field deploy) speaks the exact
# same vocabulary regardless of which app sourced the issue.

def _issue(category, rule, severity, message):
    return {"category": category, "rule": rule, "severity": severity, "message": message}


def issues_from_item(item: dict) -> list[dict]:
    checks = item.get("checks") or {}
    meta = item.get("meta") or {}
    issues = []

    if checks.get("no_title"):
        issues.append(_issue("title", "missing", "error", "Page title is missing."))
    else:
        if checks.get("title_too_short"):
            issues.append(_issue("title", "too_short", "warning", "Title tag is shorter than recommended."))
        if checks.get("title_too_long"):
            issues.append(_issue("title", "too_long", "warning", "Title tag is longer than recommended."))
        if checks.get("duplicate_title_tag"):
            issues.append(_issue("title", "duplicate", "warning", "Title tag is duplicated on another crawled page."))

    if checks.get("no_description"):
        issues.append(_issue("meta_description", "missing", "error", "Meta description is missing."))
    elif checks.get("duplicate_meta_tags"):
        issues.append(_issue("meta_description", "duplicate", "warning", "Meta tags are duplicated on another crawled page."))

    if checks.get("no_h1_tag"):
        issues.append(_issue("h1", "missing", "error", "No H1 tag found."))

    if checks.get("no_image_alt"):
        issues.append(_issue("image_alt", "missing", "warning", "One or more images are missing alt text."))

    if checks.get("canonical") is False:
        issues.append(_issue("canonical", "missing", "warning", "Canonical link is missing."))

    if checks.get("is_https") is False:
        issues.append(_issue("security", "no_ssl", "error", "Page is not served over HTTPS."))

    if checks.get("low_content_rate"):
        issues.append(_issue("content", "thin", "warning", "Page has a low ratio of text content to page size."))

    if checks.get("irrelevant_title"):
        issues.append(_issue("title", "irrelevant", "warning", "Title does not appear relevant to the page content."))

    if checks.get("irrelevant_description"):
        issues.append(_issue("meta_description", "irrelevant", "warning", "Meta description does not appear relevant to the page content."))

    social = item.get("social_media_tags") or {}
    if not social.get("og:title") and not social.get("og:description"):
        issues.append(_issue("opengraph", "missing", "warning", "OpenGraph title/description tags are missing."))
    if not social.get("twitter:card"):
        issues.append(_issue("twitter", "missing", "warning", "Twitter card meta tag is missing."))

    return issues


def normalize_page(item: dict) -> dict:
    """Maps a raw DataForSEO on-page item (from instant_pages or pages) into
    the fields models.Page stores. Field names per DataForSEO's documented
    response shape: meta.title, meta.description, meta.canonical, meta.htags,
    content.plain_text_word_count, onpage_score, checks."""
    meta = item.get("meta") or {}
    htags = meta.get("htags") or {}
    content = item.get("content") or {}
    social = item.get("social_media_tags") or {}

    return {
        "url": item.get("url"),
        "title": meta.get("title"),
        "meta_description": meta.get("description"),
        "meta_keywords": meta.get("meta_keywords"),
        "canonical": meta.get("canonical"),
        "h1": htags.get("h1") or [],
        "h2": htags.get("h2") or [],
        "image_alts": meta.get("images") or [],
        "og_title": social.get("og:title"),
        "og_description": social.get("og:description"),
        "twitter_card": social.get("twitter:card"),
        "word_count": content.get("plain_text_word_count"),
        "onpage_score": item.get("onpage_score"),
        "checks": item.get("checks") or {},
    }
