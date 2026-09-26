"""
Deterministic validation of ONE proposed edit, run before it is shown as ready for
approval and again whenever a person edits the text. No network, no model, no
database: everything a check needs is passed in `ctx`, so each check is a plain
function of its inputs.

Every check reports one of five explicit states, and the suggestion's overall state
is the worst of them:

    ok  <  warning  <  needs_human_verification  <  error  <  blocked

    warning                   a judgement call the person should look at
    needs_human_verification  new factual content: a person must check it
    error                     the check itself could not run (never silently skipped)
    blocked                   must not be approved as written

A blocked suggestion is still stored and shown, with its reasons: a validation
failure is never turned into an empty list.

The thresholds below are judgement calls that raise a flag for a person. They are
NOT ranking rules: nothing here claims that a title "must" be N characters, and
there is deliberately no keyword-density target and no word-count target.

Checks: schema, structure, keyword_repetition, duplication, competitor_copy,
fact_check, brand_tone, and (for edits that add substantial content) cannibalization.
"""
import html.parser
import re
import unicodedata
from collections import Counter

from . import action_plan, gap_analysis

TYPES = (
    "add_section", "expand_section", "rewrite_section", "improve_heading",
    "improve_title", "improve_meta_description", "add_faq", "improve_internal_link",
)
SECTION_TYPES = ("add_section", "expand_section", "rewrite_section", "add_faq", "improve_internal_link")
SHORT_FIELD_TYPES = ("improve_title", "improve_meta_description", "improve_heading")
CANNIBALIZATION_TYPES = ("add_section", "expand_section", "add_faq")

CHECK_STATUSES = ("ok", "warning", "needs_human_verification", "error", "blocked")
_RANK = {s: i for i, s in enumerate(CHECK_STATUSES)}

MIN_CHARS = {
    "improve_title": 8, "improve_meta_description": 40, "improve_heading": 3,
    "add_section": 80, "expand_section": 80, "rewrite_section": 60, "add_faq": 60, "improve_internal_link": 25,
}
# Search results truncate by pixel width, not characters, so these are only prompts to look.
TITLE_LONG, TITLE_SHORT = 65, 15
META_LONG, META_SHORT = 165, 70
HEADING_LONG = 110

KEYWORD_WARN_DELTA, KEYWORD_BLOCK_DELTA = 3, 6          # occurrences ADDED to a body of text
KEYWORD_SHORT_WARN, KEYWORD_SHORT_BLOCK = 2, 3          # occurrences IN a title / meta / heading
BIGRAM_REPEATS, TRIGRAM_REPEATS = 4, 3

SHINGLE_WORDS = 5
MIN_WORDS_TO_COMPARE = 12
OWN_PAGE_WARN, OWN_PAGE_BLOCK = 0.40, 0.70
OTHER_PAGE_WARN, OTHER_PAGE_BLOCK = 0.50, 0.80
COPY_WINDOW_LONG = action_plan.COPY_WINDOW_WORDS       # 12 consecutive words
MAX_CLAIMS = 12
MAX_TEXT_FOR_INDEX = 30000

_THIN = re.compile(r"^\s*(add|include|provide|write|expand)\s+(more|further|additional)\s+(detail|details|information|content|text)\b.{0,40}$", re.I)
_INSTRUCTION = re.compile(r"^\s*(add|include|consider|mention|explain|describe|cover|discuss|expand|improve|update)\b", re.I)


def _check(name: str, status: str, message: str | None = None, **details) -> dict:
    out = {"name": name, "status": status}
    if message:
        out["message"] = message
    if details:
        out["details"] = details
    return out


def overall_status(checks: list[dict]) -> str:
    return max((c["status"] for c in checks), key=lambda s: _RANK[s], default="ok")


def _norm(text) -> str:
    return " ".join(str(text or "").split()).casefold()


# ── 1. schema ─────────────────────────────────────────────────────────────

def target_fits(type_: str, target: str | None) -> bool:
    is_section = bool(re.fullmatch(r"sec_\d+", target or ""))
    return {
        "improve_title": target == "title",
        "improve_meta_description": target == "meta_description",
        "improve_heading": target == "h1" or is_section,
        "expand_section": is_section,
        "rewrite_section": is_section,
        "add_section": target == "new",
        "add_faq": target == "new",
        "improve_internal_link": target == "new" or is_section,
    }.get(type_, False)


def _schema(s: dict, ctx: dict) -> dict:
    t = s.get("type")
    if t not in TYPES:
        return _check("schema", "blocked", f"Unsupported suggestion type {t!r}.", fatal=True)
    after = (s.get("after") or "").strip()
    if not after:
        return _check("schema", "blocked", "The suggestion has no proposed text.", fatal=True)

    problems = []
    if not target_fits(t, s.get("target_ref")):
        problems.append(f"the target {s.get('target_ref')!r} does not fit a {t.replace('_', ' ')}")
    before = s.get("before")
    if t in ("expand_section", "rewrite_section") and not (before or "").strip():
        problems.append("the current text of that section is not available, so it cannot be rewritten or expanded safely")
    if t == "improve_heading" and s.get("target_ref") != "h1" and not (before or "").strip():
        problems.append("there is no current heading to improve")
    if not (s.get("problem") or "").strip():
        problems.append("it does not say what the problem is")
    if not s.get("evidence"):
        problems.append("it cites no evidence")
    if len(after) < MIN_CHARS[t] or _THIN.match(after):
        problems.append("it is a thin edit: it must contain the actual proposed text, not a request for more detail")
    if before and _norm(before) == _norm(after):
        problems.append("the proposed text is identical to the current text")
    if problems:
        return _check("schema", "blocked", "Blocked: " + "; ".join(problems) + ".")
    if t in SECTION_TYPES and len(after) < 120 and _INSTRUCTION.match(after):
        return _check("schema", "warning", "The text reads like an instruction ('Add...', 'Explain...') rather than finished wording.")
    return _check("schema", "ok")


# ── 2. structure ──────────────────────────────────────────────────────────

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_OPTIONAL_END = {"p", "li", "dt", "dd", "tr", "td", "th", "thead", "tbody", "tfoot", "option", "colgroup", "caption"}
_FORBIDDEN_TAGS = {"script", "style", "iframe", "object", "embed", "form", "input", "button", "textarea", "select", "link", "meta", "base", "frame", "frameset", "applet"}
_MD_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+\S", re.M)
_MD_LINK = re.compile(r"\[([^\]]+)\]\((\S+?)\)")
_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
_URL = re.compile(r"https?://[^\s)\"'<>\]]+")
_BAD_SCHEME = re.compile(r"\s*(javascript|vbscript|data):", re.I)


class _Fragment(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.blocked: list[str] = []
        self.heading_levels: list[int] = []
        self.links: list[tuple[str | None, str]] = []
        self._href = None
        self._text: list[str] = []
        self._in_link = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _FORBIDDEN_TAGS:
            self.blocked.append(f"<{tag}> is not allowed in page content")
        for key, value in attrs:
            if key.lower().startswith("on"):
                self.blocked.append(f"the '{key}' attribute is not allowed")
            if key.lower() in ("href", "src") and value and _BAD_SCHEME.match(value):
                self.blocked.append(f"a '{value.split(':')[0]}:' address is not allowed")
        if re.fullmatch(r"h[1-6]", tag):
            self.heading_levels.append(int(tag[1]))
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text, self._in_link = [], True
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID and self.stack:
            self.stack.pop()
            if tag.lower() == "a":
                self._in_link = False

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _VOID:
            return
        if tag == "a" and self._in_link:
            self.links.append((self._href, "".join(self._text).strip()))
            self._in_link = False
        if tag in self.stack:
            while self.stack:
                top = self.stack.pop()
                if top == tag:
                    break
                if top not in _OPTIONAL_END:
                    self.blocked.append(f"<{top}> was not closed before </{tag}>")
        elif tag not in _OPTIONAL_END:
            self.blocked.append(f"</{tag}> has no matching opening tag")

    def handle_data(self, data):
        if self._in_link:
            self._text.append(data)

    def finish(self):
        self.close()
        for tag in self.stack:
            if tag not in _OPTIONAL_END:
                self.blocked.append(f"<{tag}> is never closed")
        return self


def parse_fragment(text: str) -> dict:
    """{blocked: [...], heading_levels: [...], links: [(href, anchor_text)]} for a piece
    of page content that may be plain text, markdown, or simple HTML."""
    parser = _Fragment()
    if "<" in text:
        parser.feed(text)
        parser.finish()
    levels = list(parser.heading_levels) + [len(m.group(1)) for m in _MD_HEADING.finditer(text)]
    links = list(parser.links) + [(m.group(2), m.group(1)) for m in _MD_LINK.finditer(text)]
    return {"blocked": parser.blocked, "heading_levels": levels, "links": links}


def _structure(s: dict, ctx: dict) -> dict:
    t, after = s["type"], (s.get("after") or "").strip()
    blocked, warnings = [], []

    if t in SHORT_FIELD_TYPES:
        if "\n" in after:
            blocked.append("it must be a single line")
        if _TAG.search(after):
            blocked.append("it must be plain text, without HTML tags")
        n = len(after)
        if t == "improve_title":
            if n > TITLE_LONG:
                warnings.append(f"{n} characters: titles this long are often cut off in search results (the exact cut-off varies)")
            elif n < TITLE_SHORT:
                warnings.append(f"only {n} characters: very short for a page title")
        elif t == "improve_meta_description":
            if n > META_LONG:
                warnings.append(f"{n} characters: descriptions this long are often shortened in search results (the exact cut-off varies)")
            elif n < META_SHORT:
                warnings.append(f"only {n} characters: short for a meta description")
        elif n > HEADING_LONG:
            warnings.append(f"{n} characters: long for a heading")
    else:
        frag = parse_fragment(after)
        blocked += frag["blocked"]
        levels = frag["heading_levels"]
        if 1 in levels:
            blocked.append("it contains an H1, but the page already has one")
        for a, b in zip(levels, levels[1:]):
            if b - a > 1:
                warnings.append(f"the heading levels skip from H{a} to H{b}")
                break
        if t == "add_section" and not levels:
            warnings.append("a new section should start with its own heading")
        if t == "add_faq" and "?" not in after:
            warnings.append("no question was found: an FAQ entry needs the question as well as the answer")
        if t == "improve_internal_link":
            allowed = {c["url"] for c in ctx.get("candidates", [])}
            hrefs = [h for h, _ in frag["links"] if h]
            if s.get("link_target") not in allowed:
                blocked.append("the link target is not one of the site pages the tool supplied")
            if not hrefs:
                blocked.append("it contains no link")
            elif any(h != s.get("link_target") for h in hrefs):
                blocked.append("it links to an address other than the chosen internal page")
            if any(not (text or "").strip() for _, text in frag["links"]):
                blocked.append("a link has no anchor text")
            if len(hrefs) > 2:
                warnings.append(f"{len(hrefs)} links in one edit is a lot; an internal-link edit should add one or two")

    if blocked:
        return _check("structure", "blocked", "Blocked: " + "; ".join(blocked) + ".")
    if warnings:
        return _check("structure", "warning", "; ".join(warnings).capitalize() + ".")
    return _check("structure", "ok")


# ── 3. keyword repetition ─────────────────────────────────────────────────

def _count_run(seq: list[str], needle: list[str]) -> int:
    n = len(needle)
    if not n:
        return 0
    return sum(1 for i in range(len(seq) - n + 1) if seq[i:i + n] == needle)


def keyword_counts(text: str | None, keyword: str) -> dict:
    """Occurrences of the keyword in `text`: 'exact' = the same words in the same
    order; 'normalized' = the same content words in order, ignoring case, stop words
    and plurals."""
    return {
        "exact": _count_run(gap_analysis.tokenize(text or ""), gap_analysis.tokenize(keyword)),
        "normalized": _count_run(gap_analysis.normalize_sequence(text or ""), gap_analysis.normalize_sequence(keyword)),
    }


def _phrase_counts(text: str) -> tuple[Counter, Counter]:
    seq = gap_analysis.normalize_sequence(text or "")
    return (Counter(tuple(seq[i:i + 3]) for i in range(len(seq) - 2)),
            Counter(tuple(seq[i:i + 2]) for i in range(len(seq) - 1)))


def repeated_phrases(text: str, before: str | None = None) -> list[dict]:
    """Meaningful (content-word) phrases the edit REPEATS suspiciously often. Only
    repetition the edit adds counts (at least two more than `before` already had):
    a page that already repeats a phrase is not made worse by an edit that does not
    add to it, and should not be flagged for it."""
    tri_after, bi_after = _phrase_counts(text)
    tri_before, bi_before = _phrase_counts(before or "")
    # Shortest repeating unit first; a longer phrase made only of words already
    # reported is just a rotation of the same repetition ("a b a", "b a b") and is skipped.
    out = [{"phrase": " ".join(g), "count": c} for g, c in bi_after.most_common()
           if c >= BIGRAM_REPEATS and c - bi_before.get(g, 0) >= 2]
    seen = {w for p in out for w in p["phrase"].split()}
    for g, c in tri_after.most_common():
        if c >= TRIGRAM_REPEATS and c - tri_before.get(g, 0) >= 2 and not set(g) <= seen:
            out.append({"phrase": " ".join(g), "count": c})
            seen |= set(g)
    return out[:3]


def _keyword_repetition(s: dict, ctx: dict) -> dict:
    kw = ctx.get("keyword") or ""
    after, before = s.get("after") or "", s.get("before")
    a, b = keyword_counts(after, kw), keyword_counts(before, kw)
    page_text = (ctx.get("page") or {}).get("text") or ""
    page_before = keyword_counts(page_text, kw)["normalized"]
    details = {
        "exact_before": b["exact"], "exact_after": a["exact"], "normalized_before": b["normalized"], "normalized_after": a["normalized"],
        "page_normalized_before": page_before, "page_normalized_after": max(0, page_before - b["normalized"]) + a["normalized"],
        "repeated_phrases": repeated_phrases(after, before),
    }
    delta = a["normalized"] - b["normalized"]
    short = s["type"] in SHORT_FIELD_TYPES
    if short:
        level = "blocked" if a["normalized"] >= KEYWORD_SHORT_BLOCK else "warning" if a["exact"] >= KEYWORD_SHORT_WARN or a["normalized"] >= KEYWORD_SHORT_WARN else "ok"
        message = f"The keyword appears {a['normalized']} times in the proposed text (in the current text: {b['normalized']})."
    else:
        level = "blocked" if delta >= KEYWORD_BLOCK_DELTA else "warning" if delta >= KEYWORD_WARN_DELTA else "ok"
        message = f"The keyword would appear {delta} more times than it does now ({b['normalized']} now, {a['normalized']} proposed)."
    if level != "ok":
        return _check("keyword_repetition", level, message + " Read it aloud: it should not sound repetitive.", **details)
    phrases = details["repeated_phrases"]
    if phrases:
        p = phrases[0]
        return _check("keyword_repetition", "warning", f"The phrase '{p['phrase']}' is repeated {p['count']} times in the proposed text.", **details)
    return _check("keyword_repetition", "ok", **details)


# ── 4. duplication risk ───────────────────────────────────────────────────

def _shingles(words: list[str]) -> set:
    return {tuple(words[i:i + SHINGLE_WORDS]) for i in range(len(words) - SHINGLE_WORDS + 1)}


def _containment(words: list[str], shingle_set: set) -> float:
    mine = _shingles(words)
    return len(mine & shingle_set) / len(mine) if mine else 0.0


def _site_index(ctx: dict) -> dict:
    """shingle -> indexes of the site pages containing it. Built once per validation
    context, so validating several suggestions does not re-read every page."""
    if "_site_index" not in ctx:
        index: dict[tuple, set[int]] = {}
        pages = ctx.get("site_pages") or []
        for i, p in enumerate(pages):
            text = (getattr(p, "fit_markdown", None) or getattr(p, "custom_content", None) or getattr(p, "markdown", None) or "")[:MAX_TEXT_FOR_INDEX]
            for sh in _shingles(gap_analysis.tokenize(text)):
                index.setdefault(sh, set()).add(i)
        ctx["_site_index"] = index
    return ctx["_site_index"]


def _duplication(s: dict, ctx: dict) -> dict:
    """A duplication-RISK signal: how much of the proposed text already exists on this
    page or elsewhere on the site. It is not a plagiarism verdict."""
    t, after = s["type"], (s.get("after") or "").strip()
    pages = ctx.get("site_pages") or []
    if t in SHORT_FIELD_TYPES:
        field = "meta_description" if t == "improve_meta_description" else "title" if t == "improve_title" else None
        if field:
            same = [p for p in pages if _norm(getattr(p, field, None)) and _norm(getattr(p, field, None)) == _norm(after)]
            if same:
                label = "meta description" if field == "meta_description" else "title"
                return _check("duplication", "warning", f"The same {label} is already used on {same[0].url}: pages with identical {label}s compete with each other.", page=same[0].url)
        return _check("duplication", "ok")

    words = gap_analysis.tokenize(after)
    if len(words) < MIN_WORDS_TO_COMPARE:
        return _check("duplication", "ok", "Too short to compare meaningfully.")
    page_words = gap_analysis.tokenize((ctx.get("page") or {}).get("text") or "")
    own = _shingles(page_words) - _shingles(gap_analysis.tokenize(s.get("before") or ""))
    own_ratio = _containment(words, own)
    if own_ratio >= OWN_PAGE_BLOCK:
        return _check("duplication", "blocked", f"Duplication risk: about {round(own_ratio * 100)}% of this text is already on the page.", own_page=round(own_ratio, 2))
    best_i, best_ratio = None, 0.0
    if pages:
        mine = _shingles(words)
        counts = Counter(i for sh in mine for i in _site_index(ctx).get(sh, ()))
        if counts and mine:
            best_i, hits = counts.most_common(1)[0]
            best_ratio = hits / len(mine)
    if best_ratio >= OTHER_PAGE_BLOCK:
        return _check("duplication", "blocked", f"Duplication risk: about {round(best_ratio * 100)}% of this text already appears on {pages[best_i].url}.", page=pages[best_i].url, ratio=round(best_ratio, 2))
    notes = []
    if own_ratio >= OWN_PAGE_WARN:
        notes.append(f"about {round(own_ratio * 100)}% of it is already on this page")
    if best_ratio >= OTHER_PAGE_WARN:
        notes.append(f"about {round(best_ratio * 100)}% of it appears on {pages[best_i].url}")
    if notes:
        return _check("duplication", "warning", "Duplication risk: " + " and ".join(notes) + ".", own_page=round(own_ratio, 2), ratio=round(best_ratio, 2))
    return _check("duplication", "ok", own_page=round(own_ratio, 2), ratio=round(best_ratio, 2))


# ── 5. competitor wording ─────────────────────────────────────────────────

def _competitor_copy(s: dict, ctx: dict) -> dict:
    texts = ctx.get("competitor_texts") or []
    if not texts:
        return _check("competitor_copy", "ok", "No competitor text was stored for this evidence, so wording was not compared.")
    words = gap_analysis.tokenize(s.get("after") or "")
    if not words:
        return _check("competitor_copy", "ok")
    window = COPY_WINDOW_LONG if len(words) >= 30 else max(5, min(8, (len(words) + 1) // 2))
    worst = max((action_plan.longest_shared_run(words, gap_analysis.tokenize(t)) for t in texts), default=0)
    if worst >= window:
        return _check("competitor_copy", "blocked", f"Blocked: {worst} consecutive words match a competitor page. The wording must be original.", longest_shared_run=worst)
    if worst >= max(4, round(window * 0.66)):
        return _check("competitor_copy", "warning", f"{worst} consecutive words match a competitor page: reword it.", longest_shared_run=worst)
    return _check("competitor_copy", "ok", longest_shared_run=worst)


# ── 6. factual claims ─────────────────────────────────────────────────────

_FIGURE = re.compile(r"\d[\d,.]*\d|\d")
_CITATION = re.compile(r"\b(according to|as per|studies show|a study|survey|research (shows|indicates|found)|statistics|reported by|source:)\b", re.I)
_SENTENCE = re.compile(r"(?<=[.!?।])\s+|\n+")


def _figures(text: str) -> set[str]:
    return {f.rstrip(".,") for f in _FIGURE.findall(text or "")}


def _fact_check(s: dict, ctx: dict) -> dict:
    """New figures, dates, statistics, citations and links are surfaced for a person.
    Nothing is ever marked 'verified': there is no such state."""
    after = s.get("after") or ""
    page = ctx.get("page") or {}
    known = _figures(" ".join(filter(None, [
        s.get("before"), page.get("text"), page.get("title"), page.get("meta_description"),
        " ".join(e.get("label", "") for e in s.get("evidence") or []),
    ])))
    claims = [c.strip() for c in s.get("claims_to_verify") or [] if c and c.strip()]
    for sentence in (x.strip() for x in _SENTENCE.split(after)):
        if not sentence or sentence in claims:
            continue
        new_figures = _figures(sentence) - known
        if new_figures or _CITATION.search(sentence):
            claims.append(sentence)
    allowed_urls = {c["url"] for c in ctx.get("candidates", [])} | set(page.get("internal_links") or []) | ({s["link_target"]} if s.get("link_target") else set())
    page_urls = set(_URL.findall(page.get("text") or ""))
    for url in _URL.findall(after):
        if url not in allowed_urls and url not in page_urls:
            claims.append(f"Contains a link the tool did not supply: {url}")
    claims = list(dict.fromkeys(claims))[:MAX_CLAIMS]
    flagged_by_model = bool(s.get("requires_fact_check"))
    if claims:
        return _check("fact_check", "needs_human_verification", f"{len(claims)} statement(s) need to be checked against a reliable source before approval.", model_flagged=flagged_by_model, _claims=claims)
    if flagged_by_model:
        return _check("fact_check", "needs_human_verification", "The writer flagged this as containing factual statements: check them before approval.", model_flagged=True, _claims=[])
    return _check("fact_check", "ok", "No new figures, dates, citations or links were detected. Factual statements without numbers cannot be detected automatically: read it before approving.", model_flagged=False, _claims=[])


# ── 7. brand / tone ───────────────────────────────────────────────────────

_UNSAFE = [
    (re.compile(r"\b(100\s?%\s+(guarantee|guaranteed|success|selection|pass|placement)|guaranteed\s+(selection|job|success|pass|result|results|placement|admission))\b", re.I), "promises a guaranteed outcome"),
    (re.compile(r"(#\s?1\b|\bnumber\s+(one|1)\b|\bbest\s+in\s+(india|the\s+world)\b)", re.I), "makes an unverifiable 'best' claim"),
]
_FORMAL_TONE = ("formal", "professional", "authoritative", "serious", "academic")
_LANG_SCRIPT = {"hi": "DEVANAGARI", "mr": "DEVANAGARI", "ne": "DEVANAGARI", "sa": "DEVANAGARI", "kok": "DEVANAGARI",
                "en": "LATIN", "es": "LATIN", "fr": "LATIN", "de": "LATIN", "pt": "LATIN", "it": "LATIN", "id": "LATIN"}
_SCRIPT_NAME = {"DEVANAGARI": "Devanagari (Hindi)", "LATIN": "Latin (English)"}


def _script(ch: str) -> str | None:
    if not ch.isalpha():
        return None
    return (unicodedata.name(ch, "") or "").split(" ")[0] or None


def script_shares(text: str) -> dict:
    counts = Counter(sc for sc in (_script(c) for c in text or "") if sc)
    total = sum(counts.values())
    return {k: v / total for k, v in counts.items()} if total else {}


def _expected_script(page: dict) -> str | None:
    primary = ((page.get("lang") or "").split("-")[0].split("_")[0]).lower()
    if primary in _LANG_SCRIPT:
        return _LANG_SCRIPT[primary]
    shares = script_shares((page.get("text") or "")[:5000])
    top = max(shares.items(), key=lambda kv: kv[1], default=(None, 0))
    return top[0] if top[1] >= 0.7 else None


def _has_emoji(text: str) -> bool:
    return any(ord(c) >= 0x1F000 or 0x2600 <= ord(c) <= 0x27BF for c in text)


def _brand_tone(s: dict, ctx: dict) -> dict:
    after = s.get("after") or ""
    profile = ctx.get("profile")
    page = ctx.get("page") or {}
    bad = action_plan.forbidden_reason(after)
    if bad:
        return _check("brand_tone", "blocked", f"Blocked: the text {bad}.")
    issues, checked = [], ["unsafe claims"]

    for pattern, reason in _UNSAFE:
        if pattern.search(after):
            issues.append(f"it {reason}")

    letters = sum(1 for c in after if c.isalpha())
    expected = _expected_script(page)
    if expected and letters >= 12:
        checked.append("language")
        share = script_shares(after).get(expected, 0.0)
        if share < 0.3:
            issues.append(f"it is not mostly {_SCRIPT_NAME.get(expected, expected.title())}, but the page is")

    tone = ((getattr(profile, "tone", None) or "") if profile else "").lower()
    not_checked = []
    if tone and any(w in tone for w in _FORMAL_TONE):
        checked.append("tone (formal)")
        if "!" in after:
            issues.append("it uses exclamation marks, but the profile asks for a formal tone")
        if _has_emoji(after):
            issues.append("it contains emoji, but the profile asks for a formal tone")
        if len(re.findall(r"\b[A-Z]{4,}\b", after)) >= 2:
            issues.append("it uses ALL-CAPS words, but the profile asks for a formal tone")
    elif tone:
        not_checked.append("tone (only formal tones can be checked automatically)")
    else:
        not_checked.append("tone (none set in the business profile)")

    names = []
    for domain in ctx.get("competitor_domains") or []:
        label = (domain or "").lower().removeprefix("www.").split(".")[0]
        if len(label) >= 4 and re.search(rf"\b{re.escape(label)}\b", after, re.I):
            names.append(label)
    checked.append("competitor names")
    if names:
        issues.append(f"it mentions a competitor by name ({', '.join(sorted(set(names)))})")
    not_checked += ["forbidden phrases (the business profile has no such setting)", "audience"]

    note = "Checked: " + ", ".join(checked) + ". Not checked: " + ", ".join(not_checked) + "."
    if issues:
        return _check("brand_tone", "warning", "Check the wording: " + "; ".join(issues) + ". " + note)
    return _check("brand_tone", "ok", note)


# ── 8. cannibalization ────────────────────────────────────────────────────

def _cannibalization(s: dict, ctx: dict) -> dict:
    """Before adding substantial content, is another page of the site already the home
    for this keyword or topic? If so the better move may be a link, a stronger existing
    page, or a separate page: never quietly merging everything into one article."""
    labels = {e.get("label") for e in s.get("evidence") or [] if e.get("label")}
    hits = []
    for r in ctx.get("related") or []:
        kinds = [x for x in r["reasons"] if x["type"] == "keyword" or (x["type"] == "topic" and x["label"] in labels)]
        if kinds:
            hits.append({"url": r["url"], "title": r["title"], "reasons": kinds})
    if not hits:
        return _check("cannibalization", "ok")
    first = hits[0]
    if any(k["type"] == "keyword" for k in first["reasons"]):
        why = f"{first['url']} already targets this keyword"
    else:
        topic = next(k["label"] for k in first["reasons"] if k["type"] == "topic")
        why = f"{first['url']} already covers '{topic}'"
    more = f" (and {len(hits) - 1} more)" if len(hits) > 1 else ""
    return _check(
        "cannibalization", "warning",
        f"Another page may already be the home for this: {why}{more}. Consider linking to it, strengthening it, or making this a separate page instead of adding the same material here.",
        pages=hits[:5], alternatives=["internal_link", "strengthen_existing_page", "separate_page"],
    )


# ── the pipeline ──────────────────────────────────────────────────────────

_RUNNERS = [
    ("schema", _schema), ("structure", _structure), ("keyword_repetition", _keyword_repetition),
    ("duplication", _duplication), ("competitor_copy", _competitor_copy), ("fact_check", _fact_check),
    ("brand_tone", _brand_tone),
]


def validate_suggestion(s: dict, ctx: dict) -> dict:
    """{"status": <worst check>, "checks": [...], "claims": [...]}.

    s   : {type, target_ref, before, after, problem, evidence, claims_to_verify,
           requires_fact_check, link_target}
    ctx : {keyword, page (optimizer_page.build_page_model), site_pages, related, candidates,
           competitor_texts, competitor_domains, profile}
    A check that raises is reported as 'error', never skipped and never fatal."""
    runners = list(_RUNNERS)
    if s.get("type") in CANNIBALIZATION_TYPES:
        runners.append(("cannibalization", _cannibalization))
    checks, claims = [], []
    for name, fn in runners:
        try:
            result = fn(s, ctx)
        except Exception as exc:  # noqa: BLE001 -- a broken check must be visible, not fatal
            result = _check(name, "error", f"This check could not run: {exc}")
        details = result.get("details") or {}
        claims = details.pop("_claims", claims)
        if details.get("fatal"):                       # nothing else can be judged (no text / unknown type)
            details.pop("fatal")
            if not details:
                result.pop("details", None)
            checks.append(result)
            break
        if not details:
            result.pop("details", None)
        checks.append(result)
    return {"status": overall_status(checks), "checks": checks, "claims": claims}


def is_ready_for_approval(validation: dict) -> bool:
    """Blocked and error suggestions are shown, with reasons, but cannot be approved."""
    return validation.get("status") in ("ok", "warning", "needs_human_verification")
