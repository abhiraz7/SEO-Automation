import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .keyword_locations import DEFAULT_LOCATION, semrush_database
from .schemas import NormalizedKeyword


SEMRUSH_BASE = "https://api.semrush.com"
SEMRUSH_ANALYTICS = "https://api.semrush.com/analytics/v1/"

# Semrush accepts short column codes in export_columns (Ph, Nq, ...) but the
# CSV it returns uses human-readable header names ("Keyword", "Search Volume").
# Every parser here translates headers back to the request codes so the rest of
# the file speaks one vocabulary. Without this, data.get("Nq") is always None
# and every successful lookup is misread as "no data".
_HEADER_TO_CODE = {
    "Keyword": "Ph",
    "Search Volume": "Nq",
    "CPC": "Cp",
    "Competition": "Co",
    "Keyword Difficulty Index": "Kd",
    "Intent": "In",
    "Trends": "Td",
    "Number of Results": "Nr",
    "Domain": "Dn",
    "Url": "Ur",
    "Rank": "Rk",
    "Organic Keywords": "Or",
    "Organic Traffic": "Ot",
    "Organic Cost": "Oc",
}

# Semrush encodes intent as a digit; DataForSEO uses these labels natively, so
# normalize to the label vocabulary the UI already renders badges for.
_INTENT_CODES = {
    "0": "commercial",
    "1": "informational",
    "2": "navigational",
    "3": "transactional",
}


def is_configured() -> bool:
    """Whether credentials exist -- says nothing about whether they're valid.
    Surfaced by /keywords/provider-status so an unconfigured install looks
    different from a configured one with no data (spec Bug 3)."""
    return bool(os.environ.get("SEMRUSH_API_KEY", "").strip())


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.read().decode("utf-8")


def _map_headers(headers: list[str]) -> list[str]:
    return [_HEADER_TO_CODE.get(h, h) for h in headers]


def _parse_csv(text: str) -> dict:
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if len(lines) < 2:
        return {}
    headers = _map_headers(lines[0].split(";"))
    values = lines[1].split(";")
    return dict(zip(headers, values))


def _domain_only(base_url: str) -> str:
    parsed = urllib.parse.urlparse(base_url)
    return parsed.netloc or parsed.path


def fetch_domain_metrics(base_url: str) -> dict:
    """
    Returns dict with keys: authority_score, organic_traffic, organic_keywords, referring_domains, error
    All values are strings or None. error is set on failure.
    """
    api_key = os.environ.get("SEMRUSH_API_KEY", "").strip()
    if not api_key:
        return {"error": "No API key"}

    domain = _domain_only(base_url)
    result = {"authority_score": None, "organic_traffic": None, "organic_keywords": None, "referring_domains": None, "error": None}

    # 1. Domain ranks — organic traffic + keywords
    try:
        url = (
            f"{SEMRUSH_BASE}/?type=domain_ranks&key={api_key}"
            f"&export_columns=Dn,Or,Ot&domain={domain}&database=us"
        )
        data = _parse_csv(_get(url))
        result["organic_keywords"] = data.get("Or")
        result["organic_traffic"] = data.get("Ot")
    except Exception as e:
        result["error"] = f"domain_ranks: {e}"

    # 2. Backlinks overview — authority score + referring domains
    try:
        url = (
            f"{SEMRUSH_ANALYTICS}?key={api_key}&type=backlinks_overview"
            f"&target={domain}&target_type=root_domain&export_columns=ascore,domains_num"
        )
        data = _parse_csv(_get(url))
        result["authority_score"] = data.get("ascore")
        result["referring_domains"] = data.get("domains_num")
    except Exception as e:
        existing = result["error"] or ""
        result["error"] = (existing + f" | backlinks: {e}").strip(" |")

    return result


def fetch_backlinks_overview(base_url: str) -> dict:
    """
    Backlinks tab overview (Task 5.1). Separate from fetch_domain_metrics
    (which conflates domain_ranks + a narrower backlinks_overview call for
    the project dashboard) -- this requests the fuller column set: total
    backlink count and the follow/nofollow split, not just authority score
    and referring domain count.

    Returns a dict with keys: authority_score, referring_domains,
    total_backlinks, follow_links, nofollow_links, error (all values are
    strings or None; error is set on failure -- matches every other
    provider function's contract in this codebase, see semrush.py's
    module-level pattern in fetch_keyword_overview/fetch_domain_metrics).
    """
    api_key = os.environ.get("SEMRUSH_API_KEY", "").strip()
    if not api_key:
        return {"error": "No API key"}

    domain = _domain_only(base_url)
    result = {
        "authority_score": None, "referring_domains": None, "total_backlinks": None,
        "follow_links": None, "nofollow_links": None, "error": None,
    }
    try:
        url = (
            f"{SEMRUSH_ANALYTICS}?key={api_key}&type=backlinks_overview"
            f"&target={domain}&target_type=root_domain"
            f"&export_columns=ascore,total,domains_num,follows_num,nofollows_num"
        )
        data = _parse_csv(_get(url))
        result["authority_score"] = data.get("ascore")
        result["referring_domains"] = data.get("domains_num")
        result["total_backlinks"] = data.get("total")
        result["follow_links"] = data.get("follows_num")
        result["nofollow_links"] = data.get("nofollows_num")
        if not data:
            result["no_data"] = True
    except Exception as e:
        result["error"] = f"backlinks_overview: {e}"
    return result


def fetch_backlinks_list(base_url: str, limit: int = 100) -> dict:
    """Per-link backlinks report (Task 5.2's diffing source) -- distinct from
    fetch_backlinks_overview's aggregate counts. Returns {"rows": [...],
    "error": None} or {"rows": [], "error": "..."} on failure, matching this
    file's other list-fetchers (fetch_related_keywords etc.)."""
    api_key = os.environ.get("SEMRUSH_API_KEY", "").strip()
    if not api_key:
        return {"rows": [], "error": "No API key"}
    domain = _domain_only(base_url)
    try:
        url = (
            f"{SEMRUSH_ANALYTICS}?key={api_key}&type=backlinks"
            f"&target={domain}&target_type=root_domain"
            f"&export_columns=source_url,target_url,anchor,nofollow&display_limit={limit}"
        )
        rows = _parse_csv_rows(_get(url))
        return {"rows": rows, "error": None}
    except Exception as e:
        return {"rows": [], "error": f"backlinks: {e}"}


def _parse_csv_rows(text: str) -> list[dict]:
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if len(lines) < 2:
        return []
    headers = _map_headers(lines[0].split(";"))
    return [dict(zip(headers, line.split(";"))) for line in lines[1:]]


def fetch_keyword_overview(keyword: str, location: str = DEFAULT_LOCATION) -> dict:
    """
    Single-keyword lookup used for tracking + bulk analysis. Returns a dict
    with raw Semrush CSV field names (Ph, Nq, Cp, Co, Kd) plus an error key.
    Sets rate_limited=True on HTTP 429 so keyword_provider can trigger the
    DataForSEO fallback and put Semrush on cooldown, and no_data=True when the
    API answered fine but simply has nothing for this keyword (Semrush signals
    that with an "ERROR 50 :: NOTHING FOUND" body, which parses to no fields).
    """
    api_key = os.environ.get("SEMRUSH_API_KEY", "").strip()
    if not api_key:
        return {"error": "No API key"}

    database = semrush_database(location)
    if database is None:
        return {"error": f"Unsupported location: {location}"}

    result = {"Ph": keyword, "Nq": None, "Cp": None, "Co": None, "Kd": None, "In": None, "Td": None, "error": None}

    try:
        # phrase_this rather than phrase_all: same columns, same call, but it
        # also honors Td (12 monthly trend points) which phrase_all silently
        # drops -- that powers the sparkline for free.
        url = (
            f"{SEMRUSH_BASE}/?type=phrase_this&key={api_key}"
            f"&export_columns=Ph,Nq,Cp,Co,In,Td&phrase={urllib.parse.quote(keyword)}&database={database}"
        )
        data = _parse_csv(_get(url))
        result["Nq"] = data.get("Nq")
        result["Cp"] = data.get("Cp")
        result["Co"] = data.get("Co")
        result["In"] = data.get("In")
        result["Td"] = data.get("Td")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            result["rate_limited"] = True
        result["error"] = f"phrase_this: {e}"
    except Exception as e:
        result["error"] = f"phrase_this: {e}"

    # Keyword difficulty is a separate report from the overview call above.
    try:
        url = (
            f"{SEMRUSH_BASE}/?type=phrase_kdi&key={api_key}"
            f"&export_columns=Ph,Kd&phrase={urllib.parse.quote(keyword)}&database={database}"
        )
        data = _parse_csv(_get(url))
        result["Kd"] = data.get("Kd")
    except Exception as e:
        existing = result["error"] or ""
        result["error"] = (existing + f" | phrase_kdi: {e}").strip(" |") or None

    if not result["error"] and result["Nq"] is None and result["Kd"] is None:
        result["no_data"] = True

    return result


def fetch_keywords_bulk(keywords: list[str], location: str = DEFAULT_LOCATION) -> dict:
    """
    Bulk Analysis tab. NOTE: loops single-keyword lookups rather than
    Semrush's true batch export -- simplest correct MVP implementation.
    Revisit with a real batch report if per-keyword call volume becomes a
    cost problem (see keyword_provider.py routing assumptions).
    """
    return {kw: fetch_keyword_overview(kw, location) for kw in keywords}


def fetch_related_keywords(seed: str, location: str = DEFAULT_LOCATION) -> list[dict]:
    """Suggestions tab fallback. Returns raw CSV rows (Ph, Nq, Cp, Co)."""
    api_key = os.environ.get("SEMRUSH_API_KEY", "").strip()
    database = semrush_database(location)
    if not api_key or database is None:
        return []
    try:
        url = (
            f"{SEMRUSH_BASE}/?type=phrase_related&key={api_key}"
            f"&export_columns=Ph,Nq,Cp,Co,In&phrase={urllib.parse.quote(seed)}"
            f"&database={database}&display_limit=20"
        )
        return _parse_csv_rows(_get(url))
    except Exception:
        return []


def fetch_broad_matches(seed: str, location: str = DEFAULT_LOCATION, limit: int = 50) -> list[dict]:
    """phrase_fullsearch: keywords containing the seed in any form. The raw
    pool the route layer filters into preposition/comparison suggestion modes."""
    api_key = os.environ.get("SEMRUSH_API_KEY", "").strip()
    database = semrush_database(location)
    if not api_key or database is None:
        return []
    try:
        url = (
            f"{SEMRUSH_BASE}/?type=phrase_fullsearch&key={api_key}"
            f"&export_columns=Ph,Nq,Cp,Co,In&phrase={urllib.parse.quote(seed)}"
            f"&database={database}&display_limit={limit}"
        )
        return _parse_csv_rows(_get(url))
    except Exception:
        return []


def fetch_keyword_questions(seed: str, location: str = DEFAULT_LOCATION) -> list[dict]:
    """Questions tab fallback. Returns raw CSV rows (Ph, Nq)."""
    api_key = os.environ.get("SEMRUSH_API_KEY", "").strip()
    database = semrush_database(location)
    if not api_key or database is None:
        return []
    try:
        url = (
            f"{SEMRUSH_BASE}/?type=phrase_questions&key={api_key}"
            f"&export_columns=Ph,Nq&phrase={urllib.parse.quote(seed)}"
            f"&database={database}&display_limit=20"
        )
        return _parse_csv_rows(_get(url))
    except Exception:
        return []


def normalize_keyword_row(row: dict, keyword: str) -> NormalizedKeyword:
    """Maps a raw Semrush CSV row (any of the shapes above) into NormalizedKeyword."""

    def _int(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    def _float(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _trend(v):
        # Td is "1.00,0.82,..." -- 12 monthly relative-volume points, oldest first.
        try:
            points = [float(p) for p in str(v).split(",") if p.strip()]
            return points if points else None
        except ValueError:
            return None

    # Multi-intent keywords come back as e.g. "1,0" -- the first code is the
    # dominant intent, which is all the UI badge shows.
    intent_code = str(row.get("In") or "").split(",")[0]

    return NormalizedKeyword(
        keyword=row.get("Ph") or keyword,
        volume=_int(row.get("Nq")),
        difficulty=_int(row.get("Kd")),
        intent=_INTENT_CODES.get(intent_code),
        cpc=_float(row.get("Cp")),
        source="semrush",
        fetched_at=datetime.now(timezone.utc),
        trend_points=_trend(row.get("Td")) if row.get("Td") else None,
    )


def fetch_serp(keyword: str, location: str = DEFAULT_LOCATION) -> dict:
    """
    'View SERP' fallback when DataForSEO is down. phrase_organic only returns
    domain+URL (no titles/descriptions), so items carry the domain as title --
    a thinner SERP than DataForSEO's, but a real one instead of an error.
    Shaped like DataForSEO's serp result so the modal renders either source.
    """
    api_key = os.environ.get("SEMRUSH_API_KEY", "").strip()
    if not api_key:
        return {"error": "No API key"}
    database = semrush_database(location)
    if database is None:
        return {"error": f"Unsupported location: {location}"}
    try:
        url = (
            f"{SEMRUSH_BASE}/?type=phrase_organic&key={api_key}"
            f"&export_columns=Dn,Ur&phrase={urllib.parse.quote(keyword)}"
            f"&database={database}&display_limit=10"
        )
        rows = _parse_csv_rows(_get(url))
        if not rows:
            return {"keyword": keyword, "items": []}
        return {
            "keyword": keyword,
            "items": [
                {
                    "type": "organic",
                    "rank_absolute": i,
                    "title": row.get("Dn"),
                    "url": row.get("Ur"),
                    "description": None,
                }
                for i, row in enumerate(rows, start=1)
            ],
        }
    except Exception as e:
        return {"error": str(e)}


def health_check() -> dict:
    """Live credential probe for /keywords/provider-status. countapiunits is a
    free call; a numeric body means the key is valid and shows remaining units."""
    if not is_configured():
        return {"configured": False, "ok": False, "detail": "SEMRUSH_API_KEY not set"}
    api_key = os.environ.get("SEMRUSH_API_KEY", "").strip()
    try:
        body = _get(f"https://www.semrush.com/users/countapiunits.html?key={api_key}").strip()
        units = int(body)
        return {"configured": True, "ok": True, "detail": f"{units} API units remaining"}
    except ValueError:
        return {"configured": True, "ok": False, "detail": f"Unexpected response: {body[:120]}"}
    except Exception as e:
        return {"configured": True, "ok": False, "detail": str(e)}
