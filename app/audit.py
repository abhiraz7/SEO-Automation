from collections import Counter

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
