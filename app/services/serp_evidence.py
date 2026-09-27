"""
SERP evidence for the AI Competitor Gap feature: fetch the live SERP through the
EXISTING provider adapter (keyword_provider.get_serp: DataForSEO first, Semrush
fallback -- no new provider, no parallel adapter), normalise it into one shape,
classify each result, and pick a small set of COMPARABLE competitors.

Contract, same as every provider integration here: every call returns an explicit
status -- "ok", "no_data" (the provider answered with no organic results) or
"error" (it did not answer). An error is never rendered as an empty SERP.

The classification below is a set of transparent heuristics (domain lists and URL
shapes), not a model: it is deterministic, testable, and meant to be wrong
occasionally rather than opaque. It never blindly excludes big brands: forums,
marketplaces, social pages, homepages and category/search pages are excluded as
NON-COMPARABLE FORMATS; a large publisher that wrote a real article is kept.
"""
import re
from urllib.parse import parse_qs, urlparse

from .. import keyword_provider
from ..domain_utils import normalize_domain
from ..keyword_locations import DEFAULT_LOCATION

RESULT_CLASSES = (
    "direct_content", "publisher", "forum", "marketplace", "government",
    "social", "homepage", "category", "other",
)
# Classes that are not the same KIND of page as an article/guide/landing page.
EXCLUDED_CLASSES = ("forum", "marketplace", "social", "homepage", "category")

MAX_ORGANIC = 10
DEFAULT_MAX_COMPETITORS = 7

_FORUM_DOMAINS = {
    "reddit.com", "quora.com", "stackexchange.com", "stackoverflow.com", "answers.yahoo.com",
    "discourse.org", "disqus.com",
}
_FORUM_PATH = re.compile(r"/(forum|forums|thread|threads|discussion|discussions|community|communities|questions?|answers?)(/|$)", re.I)

_MARKETPLACE_DOMAINS = {
    "amazon.com", "amazon.in", "amazon.co.uk", "ebay.com", "etsy.com", "alibaba.com", "aliexpress.com",
    "flipkart.com", "walmart.com", "indiamart.com", "olx.in", "target.com", "bestbuy.com",
    "snapdeal.com", "meesho.com", "myntra.com",
}

_SOCIAL_DOMAINS = {
    "facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com", "youtube.com", "youtu.be",
    "pinterest.com", "tiktok.com", "t.me", "telegram.me", "wa.me", "threads.net", "snapchat.com",
}

_PUBLISHER_DOMAINS = {
    "nytimes.com", "bbc.com", "bbc.co.uk", "theguardian.com", "forbes.com", "cnn.com", "reuters.com",
    "timesofindia.indiatimes.com", "hindustantimes.com", "indianexpress.com", "ndtv.com", "thehindu.com",
    "wikipedia.org", "britannica.com", "healthline.com", "webmd.com", "investopedia.com",
}
_PUBLISHER_PATH = re.compile(r"/(news|article|articles|story|stories|blog/news)(/|$)", re.I)

_GOV_SUFFIXES = (".gov", ".gov.in", ".gov.uk", ".gov.au", ".gc.ca", ".nic.in", ".gob.mx", ".gouv.fr", ".bund.de")

_CATEGORY_PATH = re.compile(
    r"/(category|categories|tag|tags|topics?|collections?|search|archive|archives|shop|products?|listing|listings)(/|$)", re.I
)
_CATEGORY_QUERY_KEYS = {"s", "q", "search", "query", "keyword", "k"}


def _domain_matches(domain: str, domains: set[str]) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in domains)


def classify_result(url: str) -> str:
    """One of RESULT_CLASSES for a SERP result URL. Order matters: the format
    checks that make a page non-comparable (homepage / category) run before the
    domain-based ones, and 'direct_content' is the default for an ordinary page."""
    parsed = urlparse(url or "")
    domain = normalize_domain(parsed.netloc or url or "")
    path = (parsed.path or "").strip()
    stripped = path.strip("/")
    query_keys = {k.lower() for k in parse_qs(parsed.query)}

    if not domain:
        return "other"
    if _domain_matches(domain, _SOCIAL_DOMAINS):
        return "social"
    if _domain_matches(domain, _MARKETPLACE_DOMAINS):
        return "marketplace"
    if _domain_matches(domain, _FORUM_DOMAINS) or _FORUM_PATH.search(path):
        return "forum"
    if stripped == "" or re.fullmatch(r"index\.(html?|php)", stripped, re.I):
        return "homepage"
    if _CATEGORY_PATH.search(path) or (query_keys & _CATEGORY_QUERY_KEYS):
        return "category"
    if any(domain == s.lstrip(".") or domain.endswith(s) for s in _GOV_SUFFIXES):
        return "government"
    if _domain_matches(domain, _PUBLISHER_DOMAINS) or _PUBLISHER_PATH.search(path):
        return "publisher"
    return "direct_content"


def _paa_questions(item: dict) -> list[str]:
    out = []
    for el in item.get("items") or []:
        if isinstance(el, dict):
            q = (el.get("title") or "").strip()
        else:
            q = str(el).strip()
        if q:
            out.append(q)
    return out


def _related_queries(item: dict) -> list[str]:
    out = []
    for el in item.get("items") or []:
        q = (el.get("title") if isinstance(el, dict) else el) or ""
        q = str(q).strip()
        if q:
            out.append(q)
    return out


def normalize_serp(result: dict, device: str = "desktop") -> dict:
    """Provider-shaped SERP dict (see keyword_provider.get_serp) -> one stable
    shape. Never raises. `depth` says how much the answering provider could give:
    'full' (DataForSEO: titles, descriptions, people-also-ask, related searches)
    or 'thin' (Semrush fallback: domain + URL only), so downstream analysis can
    say "not available from this provider" instead of "none found"."""
    base = {
        "status": "error", "error": None, "source": None, "device": device, "depth": "full",
        "organic": [], "paa": [], "related": [], "features": {}, "featured_snippet": None,
    }
    if not isinstance(result, dict):
        return {**base, "error": "SERP provider returned an unexpected response"}
    if result.get("error"):
        return {**base, "error": str(result["error"])}

    source = result.get("_source")
    base["source"] = source
    base["device"] = result.get("_device") or device
    base["depth"] = "thin" if source == "semrush" else "full"
    base["features"] = result.get("features") or {}

    seen_urls: set[str] = set()
    organic: list[dict] = []
    for item in result.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "organic":
            url = (item.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            position = item.get("rank_group") or item.get("rank_absolute") or (len(organic) + 1)
            title = (item.get("title") or "").strip() or None
            organic.append({
                "position": int(position) if str(position).isdigit() else len(organic) + 1,
                "url": url,
                "domain": normalize_domain(url),
                "title": None if base["depth"] == "thin" else title,  # Semrush puts the domain in title: not a real title
                "description": (item.get("description") or "").strip() or None,
                "result_class": classify_result(url),
            })
        elif item_type == "people_also_ask":
            base["paa"].extend(_paa_questions(item))
        elif item_type == "related_searches":
            base["related"].extend(_related_queries(item))
        elif item_type == "featured_snippet" and base["featured_snippet"] is None:
            base["featured_snippet"] = {
                "title": (item.get("title") or "").strip() or None,
                "url": item.get("url"),
                "description": (item.get("description") or "").strip() or None,
            }

    organic.sort(key=lambda r: r["position"])
    base["organic"] = organic[:MAX_ORGANIC]
    base["paa"] = list(dict.fromkeys(base["paa"]))          # de-duplicate, keep order
    base["related"] = list(dict.fromkeys(base["related"]))

    if not base["organic"]:
        return {**base, "status": "no_data", "error": "The search provider returned no organic results for this keyword."}
    return {**base, "status": "ok"}


def fetch_serp_evidence(keyword: str, location: str = DEFAULT_LOCATION, device: str = "desktop") -> dict:
    """Live SERP -> normalised evidence. The ONLY paid call in the SERP half of
    the feature, made only on an explicit user action. Never raises: a provider
    crash becomes status='error' with the reason."""
    keyword = (keyword or "").strip()
    if not keyword:
        return {**normalize_serp({"error": "A keyword is required."}, device)}
    try:
        raw = keyword_provider.get_serp(keyword, location, device=device)
    except Exception as exc:  # noqa: BLE001 -- adapter bugs must not take the request down
        return normalize_serp({"error": f"SERP lookup failed: {exc}"}, device)
    return normalize_serp(raw, device)


def select_competitors(organic: list[dict], target_url: str, max_n: int = DEFAULT_MAX_COMPETITORS) -> tuple[list[dict], list[dict]]:
    """(selected, excluded) from the organic results.

    - The target's own domain is never a competitor.
    - Forums, marketplaces, social pages, homepages and category/search pages are
      excluded as non-comparable formats (recorded with a reason, not silently
      dropped).
    - One page per domain: the best-ranked one.
    - Ordinary content pages are preferred; publishers / government / other pages
      fill the remaining slots (large brands are NOT blindly excluded).
    - Fewer than max_n usable results is fine: the actual count is returned, and
      the UI shows it."""
    target_domain = normalize_domain(target_url)
    selected: list[dict] = []
    excluded: list[dict] = []
    seen_domains: set[str] = set()

    candidates = []
    for row in sorted(organic, key=lambda r: r["position"]):
        reason = None
        if target_domain and row["domain"] == target_domain:
            reason = "the target's own domain"
        elif row["result_class"] in EXCLUDED_CLASSES:
            reason = f"not a comparable page format ({row['result_class']})"
        elif row["domain"] in seen_domains:
            reason = "another page from the same domain ranks higher"
        if reason:
            excluded.append({**row, "excluded_reason": reason})
            continue
        seen_domains.add(row["domain"])
        candidates.append(row)

    preferred = [r for r in candidates if r["result_class"] == "direct_content"]
    others = [r for r in candidates if r["result_class"] != "direct_content"]
    for row in preferred + others:
        if len(selected) >= max_n:
            excluded.append({**row, "excluded_reason": f"beyond the {max_n} most comparable pages"})
        else:
            selected.append(row)

    selected.sort(key=lambda r: r["position"])
    return selected, excluded
