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

from .schemas import NormalizedKeyword

DATAFORSEO_BASE = "https://api.dataforseo.com/v3"
LOCATION_CODE_US = 2840
LANGUAGE_CODE_EN = "en"


def _auth() -> tuple[str, str] | None:
    login = os.environ.get("DATAFORSEO_LOGIN", "").strip()
    password = os.environ.get("DATAFORSEO_PASSWORD", "").strip()
    if not login or not password:
        return None
    return (login, password)


def _post(path: str, payload: list[dict]) -> dict:
    auth = _auth()
    if not auth:
        return {"error": "No DataForSEO credentials"}
    resp = httpx.post(f"{DATAFORSEO_BASE}{path}", json=payload, auth=auth, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_keyword_overview(keyword: str) -> dict:
    """Single-keyword lookup. Returns the raw first result item, or {"error": ...}."""
    try:
        data = _post(
            "/dataforseo_labs/google/keyword_overview/live",
            [{"keywords": [keyword], "location_code": LOCATION_CODE_US, "language_code": LANGUAGE_CODE_EN}],
        )
        if data.get("error"):
            return data
        items = data["tasks"][0]["result"][0]["items"]
        return items[0] if items else {"error": "No data"}
    except Exception as e:
        return {"error": str(e)}


def fetch_keywords_bulk(keywords: list[str]) -> dict:
    """Bulk Analysis fallback for keywords Semrush couldn't return."""
    try:
        data = _post(
            "/dataforseo_labs/google/keyword_overview/live",
            [{"keywords": keywords, "location_code": LOCATION_CODE_US, "language_code": LANGUAGE_CODE_EN}],
        )
        items = data["tasks"][0]["result"][0]["items"]
        by_keyword = {item.get("keyword"): item for item in items}
        return {kw: by_keyword.get(kw, {"error": "No data"}) for kw in keywords}
    except Exception as e:
        return {kw: {"error": str(e)} for kw in keywords}


def fetch_related_keywords(seed: str) -> list[dict]:
    """Suggestions tab primary source. Returns raw keyword_data dicts."""
    try:
        data = _post(
            "/dataforseo_labs/google/related_keywords/live",
            [{
                "keyword": seed,
                "location_code": LOCATION_CODE_US,
                "language_code": LANGUAGE_CODE_EN,
                "limit": 20,
            }],
        )
        items = data["tasks"][0]["result"][0]["items"]
        return [item.get("keyword_data", {}) for item in items]
    except Exception:
        return []


def fetch_keyword_questions(seed: str) -> list[dict]:
    """
    DataForSEO's Labs API has no endpoint dedicated to question-style keywords
    the way Semrush has phrase_questions -- this filters related_keywords
    client-side by question-word prefix. Simple heuristic, good enough for MVP.
    """
    QUESTION_WORDS = ("what", "how", "why", "when", "where", "who", "which", "can", "does", "is")
    related = fetch_related_keywords(seed)
    return [r for r in related if str(r.get("keyword", "")).lower().startswith(QUESTION_WORDS)]


def fetch_serp(keyword: str) -> dict:
    """Live SERP lookup for the 'View SERP' action -- intentionally not cached
    or stored anywhere (see keyword_provider.py / plan point 4)."""
    try:
        data = _post(
            "/serp/google/organic/live/advanced",
            [{
                "keyword": keyword,
                "location_code": LOCATION_CODE_US,
                "language_code": LANGUAGE_CODE_EN,
                "device": "desktop",
            }],
        )
        return data["tasks"][0]["result"][0]
    except Exception as e:
        return {"error": str(e)}


def normalize_keyword_row(row: dict, keyword: str) -> NormalizedKeyword:
    """Maps a raw DataForSEO Labs item into NormalizedKeyword."""
    if not row or row.get("error"):
        return NormalizedKeyword(keyword=keyword, source="dataforseo", fetched_at=datetime.now(timezone.utc))

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
