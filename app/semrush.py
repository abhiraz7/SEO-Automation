import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .schemas import NormalizedKeyword


SEMRUSH_BASE = "https://api.semrush.com"
SEMRUSH_ANALYTICS = "https://api.semrush.com/analytics/v1/"


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.read().decode("utf-8")


def _parse_csv(text: str) -> dict:
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if len(lines) < 2:
        return {}
    headers = lines[0].split(";")
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


def _parse_csv_rows(text: str) -> list[dict]:
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if len(lines) < 2:
        return []
    headers = lines[0].split(";")
    return [dict(zip(headers, line.split(";"))) for line in lines[1:]]


def fetch_keyword_overview(keyword: str) -> dict:
    """
    Single-keyword lookup used for tracking + bulk analysis. Returns a dict
    with raw Semrush CSV field names (Ph, Nq, Cp, Co, Kd) plus an error key.
    Sets rate_limited=True on HTTP 429 so keyword_provider can trigger the
    DataForSEO fallback and put Semrush on cooldown.
    """
    api_key = os.environ.get("SEMRUSH_API_KEY", "").strip()
    if not api_key:
        return {"error": "No API key"}

    result = {"Ph": keyword, "Nq": None, "Cp": None, "Co": None, "Kd": None, "error": None}

    try:
        url = (
            f"{SEMRUSH_BASE}/?type=phrase_all&key={api_key}"
            f"&export_columns=Ph,Nq,Cp,Co&phrase={urllib.parse.quote(keyword)}&database=us"
        )
        data = _parse_csv(_get(url))
        result["Nq"] = data.get("Nq")
        result["Cp"] = data.get("Cp")
        result["Co"] = data.get("Co")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            result["rate_limited"] = True
        result["error"] = f"phrase_all: {e}"
    except Exception as e:
        result["error"] = f"phrase_all: {e}"

    # Keyword difficulty is a separate report from the overview call above.
    try:
        url = (
            f"{SEMRUSH_BASE}/?type=phrase_kdi&key={api_key}"
            f"&export_columns=Ph,Kd&phrase={urllib.parse.quote(keyword)}&database=us"
        )
        data = _parse_csv(_get(url))
        result["Kd"] = data.get("Kd")
    except Exception as e:
        existing = result["error"] or ""
        result["error"] = (existing + f" | phrase_kdi: {e}").strip(" |") or None

    return result


def fetch_keywords_bulk(keywords: list[str]) -> dict:
    """
    Bulk Analysis tab. NOTE: loops single-keyword lookups rather than
    Semrush's true batch export -- simplest correct MVP implementation.
    Revisit with a real batch report if per-keyword call volume becomes a
    cost problem (see keyword_provider.py routing assumptions).
    """
    return {kw: fetch_keyword_overview(kw) for kw in keywords}


def fetch_related_keywords(seed: str) -> list[dict]:
    """Suggestions tab fallback. Returns raw CSV rows (Ph, Nq, Cp, Co)."""
    api_key = os.environ.get("SEMRUSH_API_KEY", "").strip()
    if not api_key:
        return []
    try:
        url = (
            f"{SEMRUSH_BASE}/?type=phrase_related&key={api_key}"
            f"&export_columns=Ph,Nq,Cp,Co&phrase={urllib.parse.quote(seed)}"
            f"&database=us&display_limit=20"
        )
        return _parse_csv_rows(_get(url))
    except Exception:
        return []


def fetch_keyword_questions(seed: str) -> list[dict]:
    """Questions tab fallback. Returns raw CSV rows (Ph, Nq)."""
    api_key = os.environ.get("SEMRUSH_API_KEY", "").strip()
    if not api_key:
        return []
    try:
        url = (
            f"{SEMRUSH_BASE}/?type=phrase_questions&key={api_key}"
            f"&export_columns=Ph,Nq&phrase={urllib.parse.quote(seed)}"
            f"&database=us&display_limit=20"
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

    return NormalizedKeyword(
        keyword=row.get("Ph") or keyword,
        volume=_int(row.get("Nq")),
        difficulty=_int(row.get("Kd")),
        intent=None,  # Semrush's classic phrase_all/phrase_related reports don't return intent
        cpc=_float(row.get("Cp")),
        source="semrush",
        fetched_at=datetime.now(timezone.utc),
    )
