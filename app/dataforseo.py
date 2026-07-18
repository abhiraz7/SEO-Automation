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
        return data["tasks"][0]["result"][0]
    except Exception as e:
        return {"error": str(e)}


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
