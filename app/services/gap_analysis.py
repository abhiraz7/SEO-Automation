"""
Deterministic gap analysis: what do the comparable ranking pages recurringly cover
that the target page does not?

EVERY number in here is computed by this module from fetched pages -- competitor
counts, coverage verdicts, formats, intent. None of it comes from a language model.
The AI later receives these as FACTS to interpret; it is never asked to produce or
correct a count (see ai_provider.generate_action_plan, which also rejects any answer
whose evidence does not match what this module supplied).

V1 gap types, and only these: topic (section) coverage, question (PAA) coverage,
query/keyword coverage, search intent, SERP/content format.

Wording rule for anything user-facing built on this: "5 of 7 comparable ranking
pages cover this topic", never "Google requires this topic". Word count is carried
as context and NEVER used as a target; keyword frequency is never a requirement.
"""
import math
import re
import unicodedata
from collections import Counter

# ── text normalisation ────────────────────────────────────────────────────



def _tokenize(text: str) -> list[str]:
    """Words of `text`, lower-cased. This is deliberately NOT a word-character
    regular expression: in Python those do not match combining marks, so a
    Devanagari word such as 'योग्यता' (vowel signs + virama) would be shredded into
    meaningless fragments and every coverage verdict on a Hindi page would be
    silently wrong. A word here is a run of letters, digits, combining marks and
    the zero-width joiners used inside Indic words."""
    words, cur = [], []
    for ch in (text or "").lower():
        if ch.isalnum() or unicodedata.category(ch)[0] == "M" or ch in ("‌", "‍"):
            cur.append(ch)
        elif cur:
            words.append("".join(cur))
            cur = []
    if cur:
        words.append("".join(cur))
    return words


_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from", "how", "in", "is", "it",
    "its", "of", "on", "or", "our", "that", "the", "their", "this", "to", "was", "what", "when", "where", "which",
    "who", "why", "will", "with", "you", "your", "my", "i", "we", "about", "into", "than", "then", "there", "these",
    "those", "if", "so", "not", "all", "any", "have", "has", "had", "should", "would", "could",
    # Hindi function words (this feature is used on Hindi/English sites)
    "और", "का", "की", "के", "है", "हैं", "में", "से", "को", "पर", "एवं", "तथा", "या", "यह", "ये", "वह", "जो",
    "कि", "भी", "था", "थी", "थे", "लिए", "करें", "कैसे", "क्या", "कब", "कहाँ", "क्यों", "कौन", "इस", "उस", "एक",
}
_GENERIC_HEADINGS = {
    "introduction", "intro", "conclusion", "summary", "overview", "table of contents", "contents", "faq", "faqs",
    "frequently asked questions", "final thoughts", "related posts", "related articles", "share this", "comments",
    "leave a reply", "about the author", "references", "sources", "read more", "you may also like", "tags", "menu",
    "search", "newsletter", "subscribe", "recent posts", "categories", "contact us", "disclaimer",
}

# Public names for other services: the AI Content Optimizer counts keyword occurrences
# and repeated phrases with the SAME word rules, so the two features can never
# disagree about what a "word" is (this matters for Hindi, see _tokenize).
tokenize = _tokenize
STOPWORDS = frozenset(_STOP)


def normalize_sequence(text: str) -> list[str]:
    """Lower-cased content words of `text` IN ORDER (repeats kept), without stop
    words, with a light plural fold ('documents' == 'document'). The single
    normalisation rule: normalize_tokens is built from it, and the optimizer counts
    keyword occurrences with it."""
    tokens = []
    for w in _tokenize(text):
        if w in _STOP or len(w) < 2 or w.isdigit():
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        tokens.append(w)
    return tokens


def normalize_tokens(text: str) -> frozenset:
    """The content words of `text` as a frozenset: order and repetition do not
    matter for coverage."""
    return frozenset(normalize_sequence(text))


def similar(a: frozenset, b: frozenset) -> bool:
    """Do two token sets describe the same topic? Jaccard >= 0.5, or (for a short
    label inside a longer one) containment >= 0.8 with at least two shared words."""
    if not a or not b:
        return False
    inter = len(a & b)
    if not inter:
        return False
    if inter / len(a | b) >= 0.5:
        return True
    return min(len(a), len(b)) >= 2 and inter / min(len(a), len(b)) >= 0.8


def _is_generic(heading: str) -> bool:
    h = " ".join((heading or "").lower().split()).strip(" :?!.-")
    return h in _GENERIC_HEADINGS


def consensus_threshold(total: int) -> int:
    """How many comparable pages must share something for it to count as a
    pattern: at least 2, and at least 40% of those analysed."""
    return max(2, math.ceil(0.4 * total))


def confidence_for(count: int, total: int) -> str:
    """How much to trust a 'count of total' pattern. Small samples cap at low."""
    if total >= 4 and count / total >= 0.7:
        return "high"
    if total >= 3 and count / total >= 0.5:
        return "medium"
    return "low"


# ── page views ────────────────────────────────────────────────────────────

def _page_views(page: dict) -> dict:
    """Pre-computed token sets for one page dict (target or competitor)."""
    headings = [h.get("text") for h in (page.get("headings") or []) if h.get("text")]
    if page.get("h1"):
        headings.append(page["h1"])
    heading_sets = [normalize_tokens(h) for h in headings]
    return {
        "headings": headings,
        "heading_sets": [s for s in heading_sets if s],
        "title_h1_set": normalize_tokens(f"{page.get('title') or ''} {page.get('h1') or ''}"),
        "text_set": normalize_tokens(page.get("text") or ""),
        "question_sets": [normalize_tokens(q) for q in (page.get("questions") or []) if q],
    }


def _coverage(tokens: frozenset, views: dict, body_ratio: float = 0.6) -> str:
    """covered = a heading/question of the page is about this; partial = the words
    appear in the body but there is no dedicated heading; missing = neither."""
    if not tokens:
        return "missing"
    if any(similar(tokens, h) for h in views["heading_sets"]) or any(similar(tokens, q) for q in views["question_sets"]):
        return "covered"
    if len(tokens & views["text_set"]) / len(tokens) >= body_ratio:
        return "partial"
    return "missing"


def _competitor_ref(c: dict, heading: str | None = None) -> dict:
    ref = {"position": c.get("position"), "domain": c.get("domain"), "url": c.get("url")}
    if heading:
        ref["heading"] = heading
    return ref


# ── topics (sections) ─────────────────────────────────────────────────────

def _cluster(items: list[tuple]) -> list[dict]:
    """Greedy, deterministic clustering of (competitor_index, text, tokens)."""
    clusters: list[dict] = []
    for idx, text, tokens in items:
        for cl in clusters:
            if similar(tokens, cl["tokens"]):
                cl["members"].append((idx, text))
                break
        else:
            clusters.append({"tokens": tokens, "members": [(idx, text)]})
    return clusters


def _label(cluster: dict) -> str:
    counts = Counter(" ".join(t.lower().split()) for _, t in cluster["members"])
    best = sorted(counts.items(), key=lambda kv: (-kv[1], len(kv[0])))[0][0]
    for _, t in cluster["members"]:
        if " ".join(t.lower().split()) == best:
            return t.strip()
    return best


def topic_gaps(target: dict, competitors: list[dict], keyword: str = "", limit: int = 12) -> list[dict]:
    """Recurring section topics among the competitors, each with the target's
    coverage. competitor_count = how many DISTINCT comparable pages have a
    heading about it."""
    total = len(competitors)
    if total < 2:
        return []
    kw_tokens = normalize_tokens(keyword)
    items = []
    for idx, c in enumerate(competitors):
        headings = [h.get("text") for h in (c.get("headings") or []) if h.get("text")]
        for text in headings:
            if _is_generic(text) or len(text) > 120:
                continue
            tokens = normalize_tokens(text)
            if not tokens or (kw_tokens and tokens <= kw_tokens):
                continue  # a heading that is just the keyword is not a "topic"
            items.append((idx, text, tokens))

    threshold = consensus_threshold(total)
    target_views = _page_views(target)
    gaps = []
    for cl in _cluster(items):
        holders = sorted({idx for idx, _ in cl["members"]})
        if len(holders) < threshold:
            continue
        coverage = _coverage(cl["tokens"], target_views)
        by_idx = {}
        for idx, text in cl["members"]:
            by_idx.setdefault(idx, text)
        gaps.append({
            "gap_type": "topic",
            "label": _label(cl),
            "target_coverage": coverage,
            "competitor_count": len(holders),
            "competitor_total": total,
            "evidence": {"competitors": [_competitor_ref(competitors[i], by_idx[i]) for i in holders]},
            "confidence": confidence_for(len(holders), total),
            "_avg_pos": sum((competitors[i].get("position") or 99) for i in holders) / len(holders),
        })
    gaps.sort(key=lambda g: (-g["competitor_count"], g["_avg_pos"], g["label"].lower()))
    for g in gaps:
        g.pop("_avg_pos", None)
    return gaps[:limit]


# ── questions ─────────────────────────────────────────────────────────────

def _competitor_answers(tokens: frozenset, c_views: dict) -> bool:
    """Does a competitor page appear to address this question: a matching
    heading/question, or (failing that) most of its words in the body."""
    if any(similar(tokens, h) for h in c_views["heading_sets"]) or any(similar(tokens, q) for q in c_views["question_sets"]):
        return True
    return bool(tokens) and len(tokens & c_views["text_set"]) / len(tokens) >= 0.7


def question_gaps(target: dict, competitors: list[dict], paa: list[str], limit: int = 12) -> list[dict]:
    """Questions worth answering: everything in the SERP's People-Also-Ask, plus
    questions that recur across competitors. Counts are computed from the pages."""
    total = len(competitors)
    if total < 2 and not paa:
        return []
    c_views = [_page_views(c) for c in competitors]
    threshold = consensus_threshold(total) if total else 2
    target_views = _page_views(target)

    items = [(-1, q, normalize_tokens(q)) for q in paa if normalize_tokens(q)]
    for idx, c in enumerate(competitors):
        for q in c.get("questions") or []:
            t = normalize_tokens(q)
            if t:
                items.append((idx, q, t))

    gaps = []
    for cl in _cluster(items):
        in_paa = any(idx == -1 for idx, _ in cl["members"])
        answering = [i for i, v in enumerate(c_views) if _competitor_answers(cl["tokens"], v)]
        if not in_paa and len(answering) < threshold:
            continue
        gaps.append({
            "gap_type": "question",
            "label": _label(cl),
            "target_coverage": _coverage(cl["tokens"], target_views, body_ratio=0.7),
            "competitor_count": len(answering),
            "competitor_total": total,
            "evidence": {"in_people_also_ask": in_paa, "competitors": [_competitor_ref(competitors[i]) for i in answering]},
            "confidence": confidence_for(len(answering), total) if total else "low",
        })
    gaps.sort(key=lambda g: (not g["evidence"]["in_people_also_ask"], -g["competitor_count"], g["label"].lower()))
    return gaps[:limit]


# ── keyword / query coverage ──────────────────────────────────────────────

def _looks_like_question(q: str) -> bool:
    q = (q or "").strip().lower()
    return q.endswith("?") or q.split(" ", 1)[0] in {"how", "what", "why", "when", "where", "who", "which", "can", "does", "is"}


def query_gaps(target: dict, competitors: list[dict], keyword: str, related: list[str], limit: int = 8) -> list[dict]:
    """Coverage of the target keyword and of related searches. The keyword itself
    is always reported (covered / partial / missing); a related search is reported
    when several comparable pages cover it. Presence, not frequency: no density."""
    total = len(competitors)
    target_views = _page_views(target)
    c_views = [_page_views(c) for c in competitors]
    threshold = consensus_threshold(total) if total else 2

    def covered_by_competitor(tokens, v):
        return bool(tokens) and (tokens <= v["title_h1_set"] or tokens <= v["text_set"] or
                                 len(tokens & v["text_set"]) / len(tokens) >= 0.8)

    def target_state(tokens):
        if not tokens:
            return "missing"
        if tokens <= target_views["title_h1_set"] or any(tokens <= h for h in target_views["heading_sets"]):
            return "covered"
        if len(tokens & target_views["text_set"]) / len(tokens) >= 0.8:
            return "partial"
        return "missing"

    gaps = []
    kw_tokens = normalize_tokens(keyword)
    if kw_tokens:
        holders = [i for i, v in enumerate(c_views) if covered_by_competitor(kw_tokens, v)]
        gaps.append({
            "gap_type": "query", "label": keyword.strip(), "target_coverage": target_state(kw_tokens),
            "competitor_count": len(holders), "competitor_total": total,
            "evidence": {"role": "target_keyword", "competitors": [_competitor_ref(competitors[i]) for i in holders]},
            "confidence": confidence_for(len(holders), total) if total else "low",
        })

    seen = {kw_tokens}
    for q in related:
        if _looks_like_question(q):
            continue  # questions are handled by question_gaps
        tokens = normalize_tokens(q)
        if not tokens or tokens in seen or tokens == kw_tokens:
            continue
        seen.add(tokens)
        holders = [i for i, v in enumerate(c_views) if covered_by_competitor(tokens, v)]
        if len(holders) < threshold:
            continue
        gaps.append({
            "gap_type": "query", "label": q.strip(), "target_coverage": target_state(tokens),
            "competitor_count": len(holders), "competitor_total": total,
            "evidence": {"role": "related_search", "competitors": [_competitor_ref(competitors[i]) for i in holders]},
            "confidence": confidence_for(len(holders), total),
        })
    return gaps[:limit]


# ── page format and search intent ─────────────────────────────────────────

FORMATS = ("guide", "list", "comparison", "product", "tool", "landing", "news", "other")
_TOOL = re.compile(r"(calculator|checker|generator|converter|simulator)|/tools?/", re.I)
_PRODUCT = re.compile(r"/(products?|shop|buy|cart|dp|store)(/|$)|\b(buy now|add to cart|price)\b", re.I)
_COMPARE = re.compile(r"\b(vs\.?|versus|compare|comparison|alternatives?)\b", re.I)
_LIST_TITLE = re.compile(r"^\s*(top\s+)?\d+\s+\w+|\b(best|top)\b.*\b\d+\b|\b\d+\s+(best|top)\b", re.I)
_GUIDE = re.compile(r"\b(guide|how to|what is|tutorial|explained|complete|step[- ]by[- ]step|eligibility|syllabus|meaning|tips)\b", re.I)
_NEWS = re.compile(r"/(news|press|notification|notifications)(/|$)", re.I)


def classify_page_format(page: dict) -> str:
    """Coarse content format of a page, from its URL, title, headings and length.
    Transparent heuristics; 'other' when nothing fits."""
    url = page.get("url") or ""
    title = page.get("title") or ""
    h1 = page.get("h1") or ""
    headings = [h.get("text") or "" for h in (page.get("headings") or [])]
    words = page.get("word_count") or len((page.get("text") or "").split())
    label = f"{title} {h1}"

    if _TOOL.search(url) or _TOOL.search(label):
        return "tool"
    if _PRODUCT.search(url):
        return "product"
    if _COMPARE.search(label):
        return "comparison"
    if _NEWS.search(url):
        return "news"
    numbered = sum(1 for h in headings if re.match(r"^\s*\d+[\).:]?\s+\S", h))
    if _LIST_TITLE.search(label) or numbered >= 4:
        return "list"
    if _GUIDE.search(label) or (words >= 500 and len(headings) >= 3):
        return "guide"
    if words < 300:
        return "landing"
    return "other"


def format_distribution(competitors: list[dict]) -> dict:
    return dict(Counter(classify_page_format(c) for c in competitors))


_INTENT_MODIFIERS = {
    "transactional": ("buy", "price", "prices", "cheap", "order", "discount", "coupon", "deal", "deals", "shop", "pricing", "cost", "near me", "book", "purchase", "hire"),
    "commercial": ("best", "top", "review", "reviews", "vs", "versus", "compare", "comparison", "alternative", "alternatives", "recommended"),
    "informational": ("how", "what", "why", "when", "who", "guide", "tutorial", "tips", "meaning", "definition", "examples", "explained", "learn", "eligibility", "syllabus", "difference", "benefits", "steps", "process"),
    "navigational": ("login", "log in", "sign in", "signin", "official", "website", "portal", ".com", ".in", "app download"),
}
INTENT_FORMATS = {
    "informational": {"guide", "list", "news", "tool", "other"},
    "commercial": {"list", "comparison", "guide"},
    "transactional": {"product", "landing", "tool"},
    "navigational": {"landing", "product", "tool", "guide", "other"},
}


def classify_intent(keyword: str, serp: dict, competitors: list[dict] | None = None) -> dict:
    """{'label', 'confidence', 'signals'}: search intent from keyword modifiers and
    SERP features/formats. Heuristic and reported WITH its signals so it can be
    challenged; 'unknown' when there is nothing to go on."""
    scores = Counter()
    signals = []
    kw = f" {(keyword or '').lower()} "
    for label, words in _INTENT_MODIFIERS.items():
        for w in words:
            if (f" {w} " in kw) or (w.startswith(".") and w in kw):
                scores[label] += 2
                signals.append(f"keyword contains '{w}' ({label})")
    first = (keyword or "").strip().lower().split(" ", 1)[0]
    if first in {"how", "what", "why", "when", "who", "which"}:
        scores["informational"] += 1
        signals.append("keyword is phrased as a question (informational)")

    features = (serp or {}).get("features") or {}
    for feature, label in (("people_also_ask", "informational"), ("featured_snippet", "informational"),
                           ("ai_overview", "informational"), ("shopping", "transactional"), ("local_pack", "transactional")):
        if features.get(feature):
            scores[label] += 1
            signals.append(f"SERP has {feature.replace('_', ' ')} ({label})")
    if features.get("ads", 0):
        scores["transactional"] += 1
        signals.append(f"SERP shows {features['ads']} ad(s) (commercial value)")

    formats = format_distribution(competitors or [])
    n = sum(formats.values())
    if n:
        for fmt, label in (("guide", "informational"), ("list", "commercial"), ("comparison", "commercial"), ("product", "transactional")):
            if formats.get(fmt, 0) / n >= 0.4:
                scores[label] += 1
                signals.append(f"{formats[fmt]} of {n} comparable pages are {fmt}s ({label})")

    if not scores:
        return {"label": "unknown", "confidence": "low", "signals": []}
    ranked = scores.most_common()
    label, top = ranked[0]
    margin = top - (ranked[1][1] if len(ranked) > 1 else 0)
    confidence = "high" if margin >= 3 and len(signals) >= 3 else "medium" if margin >= 2 else "low"
    return {"label": label, "confidence": confidence, "signals": signals}


def format_and_intent_gaps(target: dict, competitors: list[dict], intent: dict) -> list[dict]:
    """A gap only when the target page's format does not fit what the SERP rewards:
    (1) the dominant format among comparable pages differs from the target's, or
    (2) the search intent is one the target's format does not usually serve."""
    total = len(competitors)
    gaps = []
    target_fmt = classify_page_format(target)
    dist = format_distribution(competitors)

    if total >= 3:
        dominant, count = max(dist.items(), key=lambda kv: kv[1])
        if count / total >= 0.5 and dominant != target_fmt:
            gaps.append({
                "gap_type": "format",
                "label": f"Most comparable pages are {dominant} pages; this page reads as a {target_fmt} page",
                "target_coverage": "missing",
                "competitor_count": count, "competitor_total": total,
                "evidence": {"target_format": target_fmt, "dominant_format": dominant, "distribution": dist},
                "confidence": confidence_for(count, total),
            })

    label = intent.get("label")
    if label in INTENT_FORMATS and target_fmt not in INTENT_FORMATS[label] and intent.get("confidence") != "low":
        gaps.append({
            "gap_type": "intent",
            "label": f"Search intent looks {label}; this page reads as a {target_fmt} page",
            "target_coverage": "missing",
            "competitor_count": total, "competitor_total": total,
            "evidence": {"intent": label, "intent_confidence": intent.get("confidence"), "target_format": target_fmt, "signals": intent.get("signals", [])},
            "confidence": intent.get("confidence", "low"),
        })
    return gaps


# ── defaults, assembly ────────────────────────────────────────────────────

ACTIONS = ("add", "expand", "rewrite", "restructure", "leave_unchanged", "separate_page")


def default_action(gap: dict) -> str:
    """A deterministic starting recommendation for a gap (the AI plan may refine
    it). Covered means leave it alone."""
    cov, kind = gap.get("target_coverage"), gap.get("gap_type")
    if cov == "covered":
        return "leave_unchanged"
    if kind == "format":
        return "separate_page" if gap["evidence"].get("target_format") in ("product", "landing") else "restructure"
    if kind == "intent":
        return "separate_page" if gap["evidence"].get("target_format") in ("product", "landing") else "restructure"
    if kind == "query" and gap.get("evidence", {}).get("role") == "target_keyword":
        return "rewrite" if cov == "partial" else "add"
    return "expand" if cov == "partial" else "add"


def analyse(target: dict, competitors: list[dict], serp: dict, keyword: str) -> dict:
    """Everything derived from the fetched pages, in one dict:
    {gaps, intent, format_distribution, question_data_available}.
    `competitors` must be ONLY pages that were fetched successfully; the caller
    reports how many were attempted."""
    intent = classify_intent(keyword, serp, competitors)
    gaps = (
        format_and_intent_gaps(target, competitors, intent)
        + topic_gaps(target, competitors, keyword)
        + question_gaps(target, competitors, serp.get("paa") or [])
        + query_gaps(target, competitors, keyword, serp.get("related") or [])
    )
    for g in gaps:
        g["recommended_action"] = default_action(g)
    return {
        "gaps": gaps,
        "intent": intent,
        "format_distribution": format_distribution(competitors),
        # Thin providers (Semrush fallback) cannot supply People-Also-Ask at all:
        # "no PAA questions" must read as "not available", not "none found".
        "question_data_available": serp.get("depth") != "thin",
    }
