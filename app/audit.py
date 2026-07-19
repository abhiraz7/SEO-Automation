from collections import Counter
from types import SimpleNamespace

TITLE_MIN, TITLE_MAX = 30, 60
META_DESC_MIN, META_DESC_MAX = 50, 160
H1_MIN, H1_MAX = 10, 70
THIN_CONTENT_WORDS = 300


def _issue(category, rule, severity, message):
    return {"category": category, "rule": rule, "severity": severity, "message": message}


def _audit_title(page):
    title = (page.title or "").strip()
    if not title:
        return [_issue("title", "missing", "error", "Page title is missing.")]
    length = len(title)
    if length < TITLE_MIN:
        return [_issue("title", "too_short", "warning", f"Title is {length} chars (recommended {TITLE_MIN}-{TITLE_MAX}).")]
    if length > TITLE_MAX:
        return [_issue("title", "too_long", "warning", f"Title is {length} chars (recommended {TITLE_MIN}-{TITLE_MAX}).")]
    return []


def _audit_meta_description(page):
    desc = (page.meta_description or "").strip()
    if not desc:
        return [_issue("meta_description", "missing", "error", "Meta description is missing.")]
    length = len(desc)
    if length < META_DESC_MIN:
        return [_issue("meta_description", "too_short", "warning", f"Meta description is {length} chars (recommended {META_DESC_MIN}-{META_DESC_MAX}).")]
    if length > META_DESC_MAX:
        return [_issue("meta_description", "too_long", "warning", f"Meta description is {length} chars (recommended {META_DESC_MIN}-{META_DESC_MAX}).")]
    return []


def _audit_h1(page):
    h1s = [h for h in (page.h1 or []) if h and h.strip()]
    if not h1s:
        return [_issue("h1", "missing", "error", "No H1 tag found.")]

    issues = []
    if len(h1s) > 1:
        issues.append(_issue("h1", "multiple", "warning", f"{len(h1s)} H1 tags found; use only one."))

    length = len(h1s[0])
    if length < H1_MIN:
        issues.append(_issue("h1", "too_short", "warning", f"H1 is {length} chars (recommended {H1_MIN}-{H1_MAX})."))
    elif length > H1_MAX:
        issues.append(_issue("h1", "too_long", "warning", f"H1 is {length} chars (recommended {H1_MIN}-{H1_MAX})."))
    return issues


def _audit_h2(page):
    h2s = [h for h in (page.h2 or []) if h and h.strip()]
    if not h2s:
        return [_issue("h2", "missing", "warning", "No H2 tags found.")]

    seen_h1 = False
    for h in page.heading_structure or []:
        if h.get("tag") == "h1":
            seen_h1 = True
        elif h.get("tag") == "h2" and not seen_h1:
            return [_issue("h2", "poor_structure", "warning", "An H2 appears before the H1 in document order.")]
    return []


def _audit_image_alt(page):
    images = page.image_alts or []
    missing = sum(1 for img in images if img.get("alt") is None)
    empty = sum(1 for img in images if img.get("alt") == "")

    issues = []
    if missing:
        issues.append(_issue("image_alt", "missing", "warning", f"{missing} image(s) missing the alt attribute."))
    if empty:
        issues.append(_issue("image_alt", "empty", "warning", f"{empty} image(s) have empty alt text."))
    return issues


def _audit_schema(page):
    items = (page.domain_schema or []) + (page.page_schemas or [])
    if not items:
        return [_issue("schema", "missing", "error", "No structured data (JSON-LD) found.")]

    invalid = sum(1 for item in items if not item.get("@type"))
    if invalid:
        return [_issue("schema", "invalid", "warning", f"{invalid} schema item(s) missing @type.")]
    return []


def _audit_canonical(page):
    if not (page.canonical or "").strip():
        return [_issue("canonical", "missing", "warning", "Canonical link is missing.")]
    return []


def _audit_opengraph(page):
    missing = [field for field in ("og_title", "og_description") if not (getattr(page, field) or "").strip()]
    if missing:
        return [_issue("opengraph", "missing", "warning", f"Missing OpenGraph field(s): {', '.join(missing)}.")]
    return []


def _audit_twitter(page):
    if not (page.twitter_card or "").strip():
        return [_issue("twitter", "missing", "warning", "Twitter card meta tag is missing.")]
    return []


def _audit_lang(page):
    if not (page.lang or "").strip():
        return [_issue("lang", "missing", "error", "HTML lang attribute is missing.")]
    return []


def _audit_content(page):
    word_count = len((page.custom_content or "").split())
    if word_count < THIN_CONTENT_WORDS:
        return [_issue("content", "thin", "warning", f"Page has only {word_count} words (recommended {THIN_CONTENT_WORDS}+).")]
    return []


# ── Site-level security checks (Task 6.3) ────────────────────────────────
# SSL/headers/robots.txt are project-level (one check per site), not
# per-page like everything else in this file -- but Issue.page_id is
# NOT NULL, so these attach to the project's homepage Page row rather than
# needing a schema change. If the homepage hasn't been crawled yet, these
# checks are skipped (same "nothing to audit yet" behavior as run_audit
# with an empty page list) rather than attaching to an arbitrary page.
import httpx


def _audit_ssl(base_url: str) -> list[dict]:
    if not base_url.lower().startswith("https://"):
        return [_issue("security", "no_ssl", "error", "Site is not served over HTTPS.")]
    try:
        httpx.get(base_url, timeout=10, follow_redirects=True)
        return []
    except httpx.RequestError as e:
        return [_issue("security", "ssl_error", "error", f"HTTPS connection failed: {e}")]


SECURITY_HEADERS = {
    "strict-transport-security": "Strict-Transport-Security",
    "x-content-type-options": "X-Content-Type-Options",
    "x-frame-options": "X-Frame-Options",
    "content-security-policy": "Content-Security-Policy",
}


def _audit_security_headers(base_url: str) -> list[dict]:
    try:
        resp = httpx.get(base_url, timeout=10, follow_redirects=True)
    except httpx.RequestError:
        return []  # _audit_ssl already reports the connection failure -- don't duplicate it
    missing = [label for key, label in SECURITY_HEADERS.items() if key not in resp.headers]
    if not missing:
        return []
    return [_issue("security", "missing_headers", "warning", f"Missing security header(s): {', '.join(missing)}.")]


def _audit_robots_txt(base_url: str) -> list[dict]:
    robots_url = base_url.rstrip("/") + "/robots.txt"
    try:
        resp = httpx.get(robots_url, timeout=10, follow_redirects=True)
        if resp.status_code != 200:
            return [_issue("security", "robots_missing", "warning", f"robots.txt returned HTTP {resp.status_code}.")]
        return []
    except httpx.RequestError as e:
        return [_issue("security", "robots_missing", "warning", f"Could not fetch robots.txt: {e}")]


def merge_issue_dicts(a: dict, b: dict) -> dict:
    """Combines two {page_id: [issue, ...]} dicts, concatenating (not
    overwriting) when both have issues for the same page_id -- needed since
    the homepage can have both regular per-page issues AND security issues."""
    merged = {k: list(v) for k, v in a.items()}
    for page_id, issues in b.items():
        merged.setdefault(page_id, [])
        merged[page_id] = merged[page_id] + issues
    return merged


def run_security_audit(base_url: str, pages: list) -> dict:
    """Returns {page_id: [issue dict, ...]} for the project's homepage, in
    the same shape run_audit() returns -- callers merge the two dicts.
    Empty dict if the homepage hasn't been crawled yet."""
    homepage = next((p for p in pages if not p.error and p.url.rstrip("/") == base_url.rstrip("/")), None)
    if not homepage:
        return {}
    issues = _audit_ssl(base_url) + _audit_security_headers(base_url) + _audit_robots_txt(base_url)
    return {homepage.id: issues} if issues else {}


RULES = [
    _audit_title,
    _audit_meta_description,
    _audit_h1,
    _audit_h2,
    _audit_image_alt,
    _audit_schema,
    _audit_canonical,
    _audit_opengraph,
    _audit_twitter,
    _audit_lang,
    _audit_content,
]


def audit_page(page):
    """Run all single-page rules. Does not include cross-page checks like duplicates."""
    issues = []
    for rule in RULES:
        issues.extend(rule(page))
    return issues


def _duplicate_title_issues(pages):
    counts = Counter((p.title or "").strip().lower() for p in pages if (p.title or "").strip())
    issues_by_page = {}
    for page in pages:
        title = (page.title or "").strip().lower()
        if title and counts[title] > 1:
            issues_by_page[page.id] = [_issue("title", "duplicate", "warning", "Title is duplicated across multiple pages.")]
    return issues_by_page


def run_audit(pages):
    """Audit a project's pages. Returns {page_id: [issue dict, ...]}. Pages that failed to crawl are skipped."""
    crawled = [p for p in pages if not p.error]
    duplicates = _duplicate_title_issues(crawled)

    results = {}
    for page in crawled:
        issues = audit_page(page) + duplicates.get(page.id, [])
        results[page.id] = issues
    return results


def page_score(issues):
    """Stored Issue rows -> a 0-100 score (error -15, warning -5 each)."""
    score = 100
    for issue in issues:
        score -= 15 if issue.severity == "error" else 5
    return max(score, 0)


def title_checklist(page, issues):
    """Pass/fail checklist for the title rules. Presence/length are computed live (always accurate);
    the duplicate check relies on stored issues, since it needs cross-page data from the last audit run."""
    live_rules = {i["rule"] for i in _audit_title(page)}
    has_duplicate = any(i.category == "title" and i.rule == "duplicate" for i in issues)
    return [
        ("Title is present", "missing" not in live_rules),
        (f"Length is {TITLE_MIN}-{TITLE_MAX} characters", not ({"too_short", "too_long"} & live_rules)),
        ("Not duplicated on another page", not has_duplicate),
    ]


def current_value_for(page, category):
    """Return the page's actual current value for an issue category, not a description of what's
    wrong with it. Shared by the project detail template's "Current" card and the AI suggestion
    prompt context, so the category-to-field mapping lives in exactly one place.

    The "kind" key tells callers how to render/flatten the payload: text (single string), list
    (h1/h2 tags), kv (labeled pairs), images (src/alt pairs), schema (JSON-LD types + raw items),
    or markdown (a content excerpt + the full text).
    """
    if category == "title":
        return {"kind": "text", "value": page.title}
    if category == "meta_description":
        return {"kind": "text", "value": page.meta_description}
    if category == "h1":
        return {"kind": "list", "items": page.h1 or []}
    if category == "h2":
        return {"kind": "list", "items": page.h2 or []}
    if category == "canonical":
        return {"kind": "text", "value": page.canonical}
    if category == "opengraph":
        return {"kind": "kv", "items": [
            ("OG Title", page.og_title),
            ("OG Description", page.og_description),
            ("OG URL", page.og_url),
        ]}
    if category == "twitter":
        return {"kind": "kv", "items": [
            ("Twitter Card", page.twitter_card),
            ("Twitter Title", page.twitter_title),
            ("Twitter Description", page.twitter_description),
        ]}
    if category == "lang":
        return {"kind": "text", "value": page.lang}
    if category == "image_alt":
        return {"kind": "images", "items": page.image_alts or []}
    if category == "schema":
        items = (page.domain_schema or []) + (page.page_schemas or [])
        types = sorted({item.get("@type") for item in items if isinstance(item, dict) and item.get("@type")})
        return {"kind": "schema", "types": types, "raw": items}
    if category == "content":
        text = page.fit_markdown or page.custom_content or ""
        return {"kind": "markdown", "excerpt": text[:400], "full": text}
    return {"kind": "text", "value": None}


# --- Candidate-value validation (does a suggested replacement pass its own rule?) ---

_VALIDATABLE_RULES = {
    "title": _audit_title,
    "meta_description": _audit_meta_description,
    "h1": _audit_h1,
    "h2": _audit_h2,
    "canonical": _audit_canonical,
    "lang": _audit_lang,
}


def _mock_page_for(category, value):
    """Build the minimal page-like object each rule function actually reads.
    h1/h2 also carry heading_structure so the h2 order check doesn't misfire on an h1-less mock."""
    if category == "title":
        return SimpleNamespace(title=value)
    if category == "meta_description":
        return SimpleNamespace(meta_description=value)
    if category == "h1":
        h1 = [value] if value else []
        return SimpleNamespace(h1=h1, heading_structure=[{"tag": "h1", "text": value}] if value else [])
    if category == "h2":
        h2 = [value] if value else []
        structure = [{"tag": "h1", "text": "placeholder"}]
        if value:
            structure.append({"tag": "h2", "text": value})
        return SimpleNamespace(h2=h2, heading_structure=structure)
    if category == "canonical":
        return SimpleNamespace(canonical=value)
    if category == "lang":
        return SimpleNamespace(lang=value)
    return None


def validate_value(category: str, value: str) -> dict:
    """Run the real audit rule for `category` against a candidate replacement value, so
    the UI can show whether an AI suggestion would actually pass the audit that flagged
    it — instead of only restating the original issue's severity. Reuses the exact same
    rule functions run_audit() uses; there is no second copy of the thresholds anywhere.

    Only categories with a single free-text value to validate (title, meta_description,
    h1, h2, canonical, lang) are supported — image_alt/schema/opengraph/twitter/content
    are structural/aggregate checks that don't map to "does this one string pass?".
    """
    rule_fn = _VALIDATABLE_RULES.get(category)
    if not rule_fn:
        return {"applicable": False, "passed": None, "issues": []}

    mock_page = _mock_page_for(category, (value or "").strip())
    issues = rule_fn(mock_page)
    return {
        "applicable": True,
        "passed": len(issues) == 0,
        "issues": [{"rule": i["rule"], "severity": i["severity"], "message": i["message"]} for i in issues],
    }


# --- Rule reference (for the "View all rules" UI) --------------------------
# Built from the same constants the rule functions above actually enforce, so this
# can never drift out of sync with real behavior — there is no second copy of a
# threshold anywhere.

RULE_REQUIREMENTS = {
    "title": [
        {"rule": "missing", "severity": "error", "description": "A page title must be present."},
        {"rule": "too_short", "severity": "warning", "description": f"Title should be at least {TITLE_MIN} characters."},
        {"rule": "too_long", "severity": "warning", "description": f"Title should be at most {TITLE_MAX} characters."},
        {"rule": "duplicate", "severity": "warning", "description": "Title must not be duplicated on another page in the same project."},
    ],
    "meta_description": [
        {"rule": "missing", "severity": "error", "description": "A meta description must be present."},
        {"rule": "too_short", "severity": "warning", "description": f"Meta description should be at least {META_DESC_MIN} characters."},
        {"rule": "too_long", "severity": "warning", "description": f"Meta description should be at most {META_DESC_MAX} characters."},
    ],
    "h1": [
        {"rule": "missing", "severity": "error", "description": "At least one H1 tag must be present."},
        {"rule": "multiple", "severity": "warning", "description": "Only one H1 tag should be used per page."},
        {"rule": "too_short", "severity": "warning", "description": f"H1 should be at least {H1_MIN} characters."},
        {"rule": "too_long", "severity": "warning", "description": f"H1 should be at most {H1_MAX} characters."},
    ],
    "h2": [
        {"rule": "missing", "severity": "warning", "description": "At least one H2 tag should be present."},
        {"rule": "poor_structure", "severity": "warning", "description": "H2 tags must not appear before the H1 in document order."},
    ],
    "image_alt": [
        {"rule": "missing", "severity": "warning", "description": "Every image should have an alt attribute."},
        {"rule": "empty", "severity": "warning", "description": "Alt attributes should not be left empty."},
    ],
    "schema": [
        {"rule": "missing", "severity": "error", "description": "At least one JSON-LD structured data block should be present."},
        {"rule": "invalid", "severity": "warning", "description": "Every structured data item must declare an @type."},
    ],
    "canonical": [
        {"rule": "missing", "severity": "warning", "description": "A canonical link tag should be present."},
    ],
    "opengraph": [
        {"rule": "missing", "severity": "warning", "description": "Both og:title and og:description should be present."},
    ],
    "twitter": [
        {"rule": "missing", "severity": "warning", "description": "A twitter:card meta tag should be present."},
    ],
    "lang": [
        {"rule": "missing", "severity": "error", "description": "The HTML lang attribute must be present."},
    ],
    "content": [
        {"rule": "thin", "severity": "warning", "description": f"Page content should be at least {THIN_CONTENT_WORDS} words."},
    ],
    "security": [
        {"rule": "no_ssl", "severity": "error", "description": "Site must be served over HTTPS."},
        {"rule": "ssl_error", "severity": "error", "description": "HTTPS connection must succeed without errors."},
        {"rule": "missing_headers", "severity": "warning", "description": "Strict-Transport-Security, X-Content-Type-Options, X-Frame-Options, and Content-Security-Policy headers should be present."},
        {"rule": "robots_missing", "severity": "warning", "description": "robots.txt should be present and return HTTP 200."},
    ],
}
