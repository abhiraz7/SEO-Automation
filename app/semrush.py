import os
import urllib.parse
import urllib.request


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
