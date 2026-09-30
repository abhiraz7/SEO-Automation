"""
The page being optimized, as the optimizer sees it, and the other pages of the same
site. Nothing here calls the network or a model.

Two honesty rules shape this module:

* A section's text is only known when the crawler stored markdown for the page. A
  page from another source (DataForSEO / SEMrush on-page) has headings but no
  section text, so its sections carry text=None and the optimizer must NOT offer to
  expand or rewrite them: it cannot see what it would be editing. That is reported
  as "not available", never as an empty section.
* Every editable thing gets a stable id ("title", "meta_description", "h1",
  "sec_01"...). The AI names a target by id and the APPLICATION looks up the
  current ("before") text itself, so a before/after pair can never show text the
  model made up.
"""
import re
from urllib.parse import urlparse

from .. import models
from . import gap_analysis

SECTION_TEXT_CAP = 3000            # characters of a section's text kept for the model / the diff
EDIT_MAX_CHARS = 1500              # a section longer than this is not rewritten or expanded as ONE atomic edit
MIN_WORDS_FOR_CONTENT = 50         # below this (and with no headings) the page's content is treated as unknown
MAX_SITE_PAGES = 500
_MD_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def clean(text) -> str:
    return " ".join(str(text or "").split())


def word_count(text) -> int:
    return len(gap_analysis.tokenize(text or ""))


def _strip_markdown(text: str) -> str:
    return clean(_MD_LINK.sub(r"\1", text).replace("*", "").replace("`", "").replace("_", " "))


# ── the target page ───────────────────────────────────────────────────────

def _h1_values(page) -> list[str]:
    h1 = page.h1
    values = h1 if isinstance(h1, list) else ([h1] if h1 else [])
    return [clean(v) for v in values if v and clean(v)]


def _headings(page) -> list[dict]:
    out = []
    for h in page.heading_structure or []:
        if isinstance(h, dict) and h.get("tag") and clean(h.get("text")):
            out.append({"tag": str(h["tag"]).lower(), "text": clean(h["text"])})
    if out:
        return out
    out = [{"tag": "h1", "text": v} for v in _h1_values(page)]
    out += [{"tag": "h2", "text": clean(t)} for t in (page.h2 or []) if t and clean(t)]
    return out


def _markdown_sections(markdown: str) -> list[dict]:
    """One section per H2 / H3 heading, in order: {tag, heading, text}. The text runs
    to the next H2/H3 (an H4 and below stay inside their parent); an H1 ends the
    current section, since it is the page title, not a section."""
    sections: list[dict] = []
    current = None
    for line in markdown.splitlines():
        m = _MD_HEADING.match(line)
        if m:
            level = len(m.group(1))
            if level == 1:
                current = None
                continue
            if level <= 3:
                current = {"tag": f"h{level}", "heading": _strip_markdown(m.group(2)), "lines": []}
                sections.append(current)
                continue
        if current is not None:
            current["lines"].append(line)
    return [{"tag": s["tag"], "heading": s["heading"], "text": "\n".join(s["lines"]).strip()} for s in sections if s["heading"]]


def build_page_model(page) -> dict:
    """{url, lang, title, meta_description, h1, h1_count, headings, sections,
    sections_have_text, text, word_count, content_known, internal_links}.
    title / meta_description / h1 are None when the page has none (a missing value is
    a fact the optimizer may act on; it is never turned into an empty string)."""
    markdown = (page.markdown or "").strip() or (page.fit_markdown or "").strip()
    raw_sections = _markdown_sections(markdown) if markdown else []
    have_text = bool(raw_sections)
    headings = _headings(page)
    if not raw_sections:
        raw_sections = [{"tag": h["tag"], "heading": h["text"], "text": None} for h in headings if h["tag"] in ("h2", "h3")]

    sections = []
    for n, s in enumerate(raw_sections, start=1):
        text = s["text"]
        sections.append({
            "id": f"sec_{n:02d}", "tag": s["tag"], "heading": s["heading"],
            "text": text[:SECTION_TEXT_CAP] if text is not None else None,
            "truncated": bool(text is not None and len(text) > SECTION_TEXT_CAP),
            "words": word_count(text) if text is not None else None,
            # Rewriting or expanding replaces the WHOLE section body, so the model must be
            # able to see all of it, and an atomic edit should not swallow a long section.
            "editable": text is not None and len(text) <= EDIT_MAX_CHARS,
        })

    text = (page.fit_markdown or page.custom_content or page.markdown or "").strip()
    h1s = _h1_values(page)
    words = page.word_count or word_count(text)
    return {
        "url": page.url, "lang": clean(page.lang) or None,
        "title": clean(page.title) or None, "meta_description": clean(page.meta_description) or None,
        "h1": h1s[0] if h1s else None, "h1_count": len(h1s),
        "headings": headings, "sections": sections, "sections_have_text": have_text,
        "text": text, "word_count": words,
        "content_known": bool(headings) or words >= MIN_WORDS_FOR_CONTENT,
        "internal_links": [str(u) for u in (page.internal_links or []) if isinstance(u, str) and u],
    }


def find_section(model: dict, section_id: str) -> dict | None:
    return next((s for s in model["sections"] if s["id"] == section_id), None)


def keyword_facts(model: dict, keyword: str) -> dict:
    """Does each field contain every content word of the keyword? (Order does not
    matter, so 'B.Ed admission 2026' contains 'admission b ed'.) A fact about the
    page, not a target."""
    kw = gap_analysis.normalize_tokens(keyword)

    def has(text) -> bool:
        return bool(kw) and kw <= gap_analysis.normalize_tokens(text or "")

    return {
        "title": has(model["title"]), "meta_description": has(model["meta_description"]), "h1": has(model["h1"]),
        "title_length": len(model["title"] or ""), "meta_length": len(model["meta_description"] or ""),
    }


# ── the rest of the site ──────────────────────────────────────────────────

def url_key(url: str) -> str:
    """host (no www) + path (no trailing slash), lower-cased: two spellings of the
    same page compare equal, and so do the same page stored under two sources."""
    p = urlparse(url or "")
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return f"{host}{(p.path or '').rstrip('/').lower()}"


def site_pages(db, project_id: int, exclude_url: str, limit: int = MAX_SITE_PAGES) -> list:
    """The project's OTHER pages (newest first, one per URL), for duplication and
    cannibalization checks. The target page itself is excluded under every spelling
    and every source."""
    skip = url_key(exclude_url)
    seen = {skip}
    out = []
    rows = (db.query(models.Page).filter(models.Page.project_id == project_id)
            .order_by(models.Page.updated_at.desc(), models.Page.id.desc()).limit(limit * 2).all())
    for p in rows:
        key = url_key(p.url)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= limit:
            break
    return out


def _slug_words(url: str) -> str:
    return re.sub(r"[-_/.]+", " ", urlparse(url or "").path)


def related_pages(pages: list, keyword: str, topic_labels: list[str], limit: int = 5) -> list[dict]:
    """Which of the site's other pages already cover this keyword or one of these
    topics? A page matches the KEYWORD when its title/H1/URL contain the keyword's
    content words, and a TOPIC when its title, H1 or an H2 is about the same thing
    (the same word-set rule the competitor comparison uses). Ordered: keyword
    matches first, then by how many topics matched."""
    kw = gap_analysis.normalize_tokens(keyword)
    labels = [(label, gap_analysis.normalize_tokens(label)) for label in topic_labels if label]
    out = []
    for p in pages:
        h1s = _h1_values(p)
        views = [gap_analysis.normalize_tokens(p.title or "")] + [gap_analysis.normalize_tokens(h) for h in h1s]
        views += [gap_analysis.normalize_tokens(h) for h in (p.h2 or []) if h]
        headline = gap_analysis.normalize_tokens(f"{p.title or ''} {' '.join(h1s)} {_slug_words(p.url)}")
        reasons = []
        if kw and (kw <= headline or (len(kw) >= 3 and len(kw & headline) / len(kw) >= 0.75)):
            reasons.append({"type": "keyword"})
        for label, tokens in labels:
            if tokens and any(gap_analysis.similar(tokens, v) for v in views if v):
                reasons.append({"type": "topic", "label": label})
        if reasons:
            out.append({"page_id": p.id, "url": p.url, "title": clean(p.title) or (h1s[0] if h1s else p.url), "reasons": reasons})
    out.sort(key=lambda r: (not any(x["type"] == "keyword" for x in r["reasons"]), -len(r["reasons"]), r["url"]))
    return out[:limit]


def internal_link_candidates(related: list[dict], model: dict, limit: int = 5) -> list[dict]:
    """Related pages the target does not already link to: the only URLs the optimizer
    may propose as an internal link (it can never invent one)."""
    linked = {url_key(u) for u in model["internal_links"]}
    return [{"url": r["url"], "title": r["title"]} for r in related if url_key(r["url"]) not in linked][:limit]
