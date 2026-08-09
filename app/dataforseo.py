"""
DataForSEO adapter -- normalizes DataForSEO Labs/SERP responses into
schemas.NormalizedKeyword, mirroring semrush.py's role for the other provider.
Nothing outside this file (and semrush.py) should know DataForSEO's response
shape; keyword_provider.py is the only caller.

Auth: DataForSEO uses HTTP Basic Auth with a login/password pair (not a
single API key like Semrush), read from DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD.
"""
import os
from datetime import datetime, timezone

import httpx

from .keyword_locations import DEFAULT_LOCATION, dataforseo_location_code
from .schemas import NormalizedKeyword

DATAFORSEO_BASE = "https://api.dataforseo.com/v3"
LANGUAGE_CODE_EN = "en"


def _location_code(location: str) -> int | None:
    """None means unsupported -- callers return an explicit error rather than
    silently falling back to another market (spec Bug 2)."""
    return dataforseo_location_code(location)


def _auth() -> tuple[str, str] | None:
    login = os.environ.get("DATAFORSEO_LOGIN", "").strip()
    password = os.environ.get("DATAFORSEO_PASSWORD", "").strip()
    if not login or not password:
        return None
    return (login, password)


def is_configured() -> bool:
    """Whether credentials exist -- says nothing about whether they're valid.
    Surfaced by /keywords/provider-status so an unconfigured install looks
    different from a configured one with no data (spec Bug 3)."""
    return _auth() is not None


def health_check() -> dict:
    """Live credential probe for /keywords/provider-status. /appendix/user_data
    is a zero-cost call; it surfaces account-level failures (bad password,
    unverified account, exhausted balance) that mere env-var presence can't --
    an unverified DataForSEO account 403s every real call while looking
    perfectly 'configured'."""
    auth = _auth()
    if not auth:
        return {"configured": False, "ok": False, "detail": "DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD not set"}
    try:
        # user_data alone is NOT enough: it answers 20000 even for accounts
        # blocked from the real API (e.g. unverified email -> 40104 on every
        # Labs/SERP call). Probe a free Labs metadata endpoint too, which sits
        # behind the same entitlement wall as the paid calls.
        resp = httpx.get(f"{DATAFORSEO_BASE}/dataforseo_labs/locations_and_languages", auth=auth, timeout=15)
        data = resp.json()
        if data.get("status_code") != 20000:
            return {"configured": True, "ok": False, "detail": data.get("status_message") or f"HTTP {resp.status_code}"}

        resp = httpx.get(f"{DATAFORSEO_BASE}/appendix/user_data", auth=auth, timeout=10)
        data = resp.json()
        task = (data.get("tasks") or [{}])[0]
        money = ((task.get("result") or [{}])[0].get("money") or {}) if task.get("status_code") == 20000 else {}
        balance = money.get("balance")
        detail = f"${balance} balance remaining" if balance is not None else "OK"
        return {"configured": True, "ok": True, "detail": detail}
    except Exception as e:
        return {"configured": True, "ok": False, "detail": str(e)}


def _post(path: str, payload: list[dict]) -> dict:
    auth = _auth()
    if not auth:
        return {"error": "No DataForSEO credentials"}
    resp = httpx.post(f"{DATAFORSEO_BASE}{path}", json=payload, auth=auth, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_keyword_overview(keyword: str, location: str = DEFAULT_LOCATION) -> dict:
    """Single-keyword lookup. Returns the raw first result item, {"no_data": True}
    when the API succeeded but has nothing for this keyword, or {"error": ...}
    when the call itself failed. Callers must branch on these -- an error must
    never be rendered as an empty-but-successful row."""
    code = _location_code(location)
    if code is None:
        return {"error": f"Unsupported location: {location}"}
    try:
        data = _post(
            "/dataforseo_labs/google/keyword_overview/live",
            [{"keywords": [keyword], "location_code": code, "language_code": LANGUAGE_CODE_EN}],
        )
        if data.get("error"):
            return data
        items = data["tasks"][0]["result"][0]["items"]
        return items[0] if items else {"no_data": True}
    except Exception as e:
        return {"error": str(e)}


def fetch_keywords_bulk(keywords: list[str], location: str = DEFAULT_LOCATION) -> dict:
    """Bulk Analysis fallback for keywords Semrush couldn't return.
    Same per-keyword contract as fetch_keyword_overview: raw item,
    {"no_data": True}, or {"error": ...}."""
    code = _location_code(location)
    if code is None:
        return {kw: {"error": f"Unsupported location: {location}"} for kw in keywords}
    try:
        data = _post(
            "/dataforseo_labs/google/keyword_overview/live",
            [{"keywords": keywords, "location_code": code, "language_code": LANGUAGE_CODE_EN}],
        )
        if data.get("error"):
            return {kw: data for kw in keywords}
        items = data["tasks"][0]["result"][0]["items"]
        by_keyword = {item.get("keyword"): item for item in items}
        return {kw: by_keyword.get(kw, {"no_data": True}) for kw in keywords}
    except Exception as e:
        return {kw: {"error": str(e)} for kw in keywords}


def fetch_related_keywords(seed: str, location: str = DEFAULT_LOCATION) -> list[dict]:
    """Suggestions tab primary source. Returns raw keyword_data dicts."""
    code = _location_code(location)
    if code is None:
        return []
    try:
        data = _post(
            "/dataforseo_labs/google/related_keywords/live",
            [{
                "keyword": seed,
                "location_code": code,
                "language_code": LANGUAGE_CODE_EN,
                "limit": 20,
            }],
        )
        items = data["tasks"][0]["result"][0]["items"]
        return [item.get("keyword_data", {}) for item in items]
    except Exception:
        return []


def fetch_keyword_questions(seed: str, location: str = DEFAULT_LOCATION) -> list[dict]:
    """
    DataForSEO's Labs API has no endpoint dedicated to question-style keywords
    the way Semrush has phrase_questions -- this filters related_keywords
    client-side by question-word prefix. Simple heuristic, good enough for MVP.
    """
    QUESTION_WORDS = ("what", "how", "why", "when", "where", "who", "which", "can", "does", "is")
    related = fetch_related_keywords(seed, location)
    return [r for r in related if str(r.get("keyword", "")).lower().startswith(QUESTION_WORDS)]


# DataForSEO SERP item types -> the feature flags the UI shows as icons.
# Anything not mapped just doesn't show -- unknown new SERP furniture should
# never crash the summary.
_SERP_FEATURE_TYPES = {
    "ai_overview": "ai_overview",
    "featured_snippet": "featured_snippet",
    "people_also_ask": "people_also_ask",
    "local_pack": "local_pack",
    "map": "local_pack",
    "video": "video",
    "images": "images",
    "shopping": "shopping",
    "popular_products": "shopping",
}


def summarize_serp_features(result: dict) -> dict:
    """Boils a raw SERP result's item list down to {feature: True, ads: N}.
    Feeds both the feature icons in the UI and keyword_scoring's SERP penalty."""
    features: dict = {"ads": 0}
    for item in result.get("items") or []:
        item_type = item.get("type")
        if item_type == "paid":
            features["ads"] += 1
        mapped = _SERP_FEATURE_TYPES.get(item_type)
        if mapped:
            features[mapped] = True
    return features


def fetch_serp(keyword: str, location: str = DEFAULT_LOCATION) -> dict:
    """Live SERP lookup for the 'View SERP' action -- intentionally not cached
    or stored anywhere (see keyword_provider.py / plan point 4)."""
    code = _location_code(location)
    if code is None:
        return {"error": f"Unsupported location: {location}"}
    try:
        data = _post(
            "/serp/google/organic/live/advanced",
            [{
                "keyword": keyword,
                "location_code": code,
                "language_code": LANGUAGE_CODE_EN,
                "device": "desktop",
            }],
        )
        result = data["tasks"][0]["result"][0]
        result["features"] = summarize_serp_features(result)
        return result
    except Exception as e:
        return {"error": str(e)}


def _domain_only(base_url: str) -> str:
    return base_url.replace("https://", "").replace("http://", "").split("/")[0]


def fetch_backlinks_overview(base_url: str) -> dict:
    """Mirrors semrush.fetch_backlinks_overview's dict contract (authority_score,
    referring_domains, total_backlinks, follow_links, nofollow_links, error) so
    backlinks_provider.py can treat both sources identically for those 5 shared
    fields, plus 4 extra DataForSEO-only fields (spam_score, broken_backlinks,
    tld_distribution, platform_distribution) that BacklinksOverview now carries.

    Field mapping verified against a real live call (2026-08-09), not guessed:
    - authority_score <- rank (DataForSEO's own 0-1000 domain rank, not
      directly comparable to Semrush's Authority Score scale -- same
      *purpose*, different *scale*. UI must not imply they're the same number.)
    - follow/nofollow split <- referring_pages minus referring_pages_nofollow.
      DataForSEO's summary has no single "total backlinks that are dofollow"
      field (that would need aggregating the full per-link list, a separate,
      far more expensive call) -- referring_pages is the closest proxy the
      summary endpoint actually returns.
    """
    if not is_configured():
        return {"error": "DataForSEO not configured"}

    domain = _domain_only(base_url)
    result = {
        "authority_score": None, "referring_domains": None, "total_backlinks": None,
        "follow_links": None, "nofollow_links": None, "error": None,
    }
    try:
        data = _post("/backlinks/summary/live", [{"target": domain}])
        if data.get("status_code") != 20000:
            result["error"] = data.get("status_message") or f"status {data.get('status_code')}"
            return result

        task = (data.get("tasks") or [{}])[0]
        if task.get("status_code") != 20000:
            result["error"] = task.get("status_message") or f"task status {task.get('status_code')}"
            return result

        rows = task.get("result")
        if not rows:
            result["no_data"] = True
            return result

        row = rows[0]
        referring_pages = row.get("referring_pages") or 0
        referring_pages_nofollow = row.get("referring_pages_nofollow") or 0
        result.update({
            "authority_score": row.get("rank"),
            "referring_domains": row.get("referring_domains"),
            "total_backlinks": row.get("backlinks"),
            "follow_links": referring_pages - referring_pages_nofollow,
            "nofollow_links": referring_pages_nofollow,
            "spam_score": row.get("backlinks_spam_score"),
            "broken_backlinks": row.get("broken_backlinks"),
            "tld_distribution": row.get("referring_links_tld"),
            "platform_distribution": row.get("referring_links_platform_types"),
        })
        return result
    except Exception as e:
        result["error"] = f"backlinks_summary: {e}"
        return result


def fetch_backlinks_list(base_url: str, limit: int = 100) -> dict:
    """Mirrors semrush.fetch_backlinks_list's {"rows": [...], "error": None}
    contract. Each row maps to the same keys backlinks_provider.py already
    reads from Semrush's CSV rows (source_url, target_url, anchor, nofollow),
    plus DataForSEO's own is_new/is_lost flags (Semrush's per-link report has
    no equivalent -- our own backlink_pull.py diffing job computes new/lost
    itself instead)."""
    if not is_configured():
        return {"rows": [], "error": "DataForSEO not configured"}

    domain = _domain_only(base_url)
    try:
        data = _post("/backlinks/backlinks/live", [{"target": domain, "limit": limit, "mode": "as_is"}])
        if data.get("status_code") != 20000:
            return {"rows": [], "error": data.get("status_message") or f"status {data.get('status_code')}"}

        task = (data.get("tasks") or [{}])[0]
        if task.get("status_code") != 20000:
            return {"rows": [], "error": task.get("status_message") or f"task status {task.get('status_code')}"}

        items = ((task.get("result") or [{}])[0] or {}).get("items") or []
        rows = [
            {
                "source_url": item.get("url_from"),
                "target_url": item.get("url_to"),
                "anchor": item.get("anchor"),
                "nofollow": "false" if item.get("dofollow") else "true",
                "is_new": item.get("is_new"),
                "is_lost": item.get("is_lost"),
                "domain_rank": item.get("domain_from_rank"),
                "spam_score": item.get("backlink_spam_score"),
                "first_seen": item.get("first_seen"),
            }
            for item in items
        ]
        return {"rows": rows, "error": None}
    except Exception as e:
        return {"rows": [], "error": f"backlinks_list: {e}"}


def normalize_keyword_row(row: dict, keyword: str) -> NormalizedKeyword:
    """Maps a raw DataForSEO Labs item into NormalizedKeyword. Only ever called
    on successful rows -- error/no_data results are handled by keyword_provider,
    which builds an explicit non-ok NormalizedKeyword instead of a fake blank one."""
    keyword_info = row.get("keyword_info") or {}
    keyword_props = row.get("keyword_properties") or {}
    intent_info = row.get("search_intent_info") or {}

    return NormalizedKeyword(
        keyword=row.get("keyword") or keyword,
        volume=keyword_info.get("search_volume"),
        difficulty=keyword_props.get("keyword_difficulty"),
        intent=intent_info.get("main_intent"),
        cpc=keyword_info.get("cpc"),
        source="dataforseo",
        fetched_at=datetime.now(timezone.utc),
    )
