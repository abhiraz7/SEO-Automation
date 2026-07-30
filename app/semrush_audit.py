"""SEMrush Projects / Site Audit API client -- separate from semrush.py
(the Analytics API client, which stays untouched). This talks to the
management/v1 and reports/v1 surfaces that back SEMrush's "Site Audit"
product, used to source on-page issues instead of our own crawler-based
rule engine (see prompts/semrush-onpage-audit-plan.md).

No issue-fetching or mapping here yet -- that's Task 1.2 / 2.1. This module
only gets a domain to the point of having a running Site Audit snapshot,
and returns whatever JSON SEMrush hands back, unmapped.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

SEMRUSH_MGMT_BASE = "https://api.semrush.com/management/v1"
SEMRUSH_REPORTS_BASE = "https://api.semrush.com/reports/v1"

# MVP scope (2026-07-29): the account has 14 real SEMrush Projects, none of
# them our own vseo.vtraffic.io (Projects cap 14/14 blocks adding it -- see
# AgentLog). Rather than pull on-page data for 13 unrelated client sites,
# the MVP hard-pins to the one project we've verified end-to-end all
# session (vtraffic.io, real data, real snapshots). Revisit once the
# account either frees a slot for vseo.vtraffic.io or this needs to cover
# more than one project.
ACTIVE_SEMRUSH_PROJECT_ID = 23744438


def _api_key() -> str:
    return os.environ.get("SEMRUSH_API_KEY", "").strip()


def is_configured() -> bool:
    return bool(_api_key())


def _request(method: str, url: str, body: dict | None = None) -> dict:
    """Every management/v1 and reports/v1 call is JSON in, JSON out --
    unlike semrush.py's CSV-based Analytics endpoints. Raises on transport/
    HTTP errors rather than swallowing them into a result dict, since Task
    1.1's own verification step is a REPL call where the raw exception is
    exactly what you want to see."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        raise urllib.error.HTTPError(e.url, e.code, f"{e.reason}: {raw}", e.headers, None)
    return json.loads(raw) if raw.strip() else {}


def _domain_only(base_url: str) -> str:
    parsed = urllib.parse.urlparse(base_url)
    return parsed.netloc or parsed.path


def list_projects() -> list[dict]:
    """GET management/v1/projects -- all SEMrush Projects on this account.
    Returns a bare JSON list (confirmed against the live API -- NOT wrapped
    in {"data": [...]}). Each entry's domain lives under "url", not
    "domain". Used by get_or_create_project to check whether a project
    already exists before creating a duplicate."""
    api_key = _api_key()
    url = f"{SEMRUSH_MGMT_BASE}/projects?key={api_key}"
    result = _request("GET", url)
    return result if isinstance(result, list) else result.get("data", [])


def create_project(base_url: str) -> dict:
    """POST management/v1/projects -- registers a new SEMrush Project for
    this domain. Returns the raw created-project JSON (includes its
    project_id, needed by every call below)."""
    api_key = _api_key()
    domain = _domain_only(base_url)
    url = f"{SEMRUSH_MGMT_BASE}/projects?key={api_key}"
    return _request("POST", url, {"url": domain, "project_name": domain})


def get_or_create_project(base_url: str) -> dict:
    """Finds an existing SEMrush Project for this domain, or creates one.
    Site Audit (and every endpoint below) is scoped to a SEMrush project_id,
    not a raw domain string, so this has to run before enable/launch."""
    domain = _domain_only(base_url)
    for project in list_projects():
        if project.get("url") == domain or project.get("domain_unicode") == domain:
            return project
    return create_project(base_url)


def enable_site_audit(project_id: int, limit: int = 100) -> dict:
    """management/v1/projects/{ID}/siteaudit 'enable'/'save' -- turns on the
    Site Audit tool for a SEMrush project and configures the crawl (page
    limit, etc). Must run once before launch_site_audit will accept a
    launch call on a fresh project."""
    api_key = _api_key()
    url = f"{SEMRUSH_MGMT_BASE}/projects/{project_id}/siteaudit/settings?key={api_key}"
    return _request("POST", url, {"limit": limit})


def launch_site_audit(project_id: int) -> dict:
    """reports/v1/projects/{ID}/siteaudit 'launch' -- starts a Site Audit
    crawl on an already-enabled project. Returns the raw launch response
    (typically includes a snapshot/report id to poll -- Task 1.2)."""
    api_key = _api_key()
    url = f"{SEMRUSH_REPORTS_BASE}/projects/{project_id}/siteaudit/launch?key={api_key}"
    return _request("POST", url)


def list_snapshots(project_id: int) -> list[dict]:
    """GET reports/v1/projects/{ID}/siteaudit/snapshots -- every finished
    audit run for this project, newest first, as [{snapshot_id,
    finish_date}, ...]. Confirmed live against a real project."""
    api_key = _api_key()
    url = f"{SEMRUSH_REPORTS_BASE}/projects/{project_id}/siteaudit/snapshots?key={api_key}"
    result = _request("GET", url)
    snapshots = result.get("snapshots", [])
    return sorted(snapshots, key=lambda s: s.get("finish_date", 0), reverse=True)


def get_snapshot(project_id: int, snapshot_id: str) -> dict:
    """GET reports/v1/projects/{ID}/siteaudit/snapshot?snapshot_id=... --
    the overview for one finished audit run. Confirmed live fields:
    aiSearchScore ({value, delta}), errors/warnings (each a list of
    {id, count, delta, checks} keyed by SEMrush's numeric issue-type id --
    Task 2.1's mapping input), isPagesLimitReached, thematicScores."""
    api_key = _api_key()
    url = f"{SEMRUSH_REPORTS_BASE}/projects/{project_id}/siteaudit/snapshot?snapshot_id={snapshot_id}&key={api_key}"
    return _request("GET", url)


# Per the plan's own cost-control note: snapshot/{id}/issue/{issueId}
# costs 100 units per call (vs 1,000/page for page/{pageId} detail calls).
UNITS_PER_ISSUE_CALL = 100


def estimate_refresh_cost(project_count: int) -> int:
    """Units a full "Refresh from SEMrush" click will burn: one
    fetch_issue call per mapped issue type, per project. Shown to the user
    BEFORE they click refresh, since GET /onpage-semrush itself (reading
    cached SemrushOnPageSnapshot rows) costs zero units."""
    return project_count * len(ONPAGE_ISSUE_MAP) * UNITS_PER_ISSUE_CALL


def fetch_issue(project_id: int, snapshot_id: str, issue_id: int) -> dict:
    """GET snapshot/{id}/issue/{issueId} -- affected pages for ONE issue
    type. Confirmed live and NOT subject to the account's "Api units
    balance is zero" block that hits get_snapshot's full overview call
    (verified 2026-07-29: this returned real data for issue_ids 6/15/16/18
    while get_snapshot 403'd at the same time). Returns {"total": <count>,
    "data": [...]}; Task 2.1's mapping input, per the plan's cost-control
    call (100 units, vs 1,000/page for page/{pageId} detail calls)."""
    api_key = _api_key()
    url = f"{SEMRUSH_REPORTS_BASE}/projects/{project_id}/siteaudit/snapshot/{snapshot_id}/issue/{issue_id}?key={api_key}"
    return _request("GET", url)


# Task 2.1 groundwork: SEMrush numeric issue-type ID -> our on-page category
# + severity. IDs/names confirmed against SEMrush's public API docs
# (developer.semrush.com/api/v3/projects/site-audit/) AND cross-checked
# live: fetch_issue(6)/(15)/(16)/(18) on a real snapshot returned exactly
# the duplicate-title/duplicate-meta-description/robots.txt/sitemap.xml
# pages their docs say those IDs mean. Only covers the plan's stated
# on-page scope (title, meta description, H1, alt text, duplicate
# title/content) -- NOT the full ~80+ id catalog (broken links, 4xx/5xx,
# security, performance, etc -- out of scope here).
ONPAGE_ISSUE_MAP = {
    3: ("title", "error", "Title tag is missing or empty"),
    6: ("title", "error", "Duplicate title tag"),
    101: ("title", "warning", "Title element is too short"),
    102: ("title", "warning", "Title element is too long"),
    106: ("meta_description", "warning", "Missing meta description"),
    15: ("meta_description", "error", "Duplicate meta descriptions"),
    103: ("h1", "error", "Missing H1"),
    104: ("h1", "warning", "Multiple H1 tags"),
    110: ("image_alt", "warning", "Missing ALT attributes"),
    7: ("content", "error", "Duplicate content"),
}


def fetch_onpage_issue_counts(project_id: int, snapshot_id: str) -> dict:
    """Real on-page issue counts + affected-page rows for one project's
    latest snapshot, using ONPAGE_ISSUE_MAP + the working per-issue
    endpoint (fetch_issue) -- deliberately NOT get_snapshot's full
    overview, which is blocked by the account's separate Site Audit units
    balance right now. Costs one fetch_issue call per mapped issue type
    (10 today) -- call sparingly (behind an explicit refresh action, not
    on every page load) since each call is billed.

    Returns {"total": int, "critical": int, "warnings": int,
    "by_category": {category: count}, "rows": [{category, severity,
    message, url}, ...], "error": str|None}. "rows" is what powers the
    project_detail.html-style "Issues by category" accordion -- one row
    per affected page, per issue type. A single fetch_issue failure (e.g.
    units genuinely exhausted) is recorded in "error" but doesn't stop the
    rest from being counted."""
    result = {"total": 0, "critical": 0, "warnings": 0, "by_category": {}, "rows": [], "error": None}
    for issue_id, (category, severity, name) in ONPAGE_ISSUE_MAP.items():
        try:
            data = fetch_issue(project_id, snapshot_id, issue_id)
        except Exception as e:
            result["error"] = result["error"] or str(e)
            continue
        count = data.get("total", 0)
        result["total"] += count
        result["by_category"][category] = result["by_category"].get(category, 0) + count
        if severity == "error":
            result["critical"] += count
        else:
            result["warnings"] += count
        for page in data.get("data", []):
            url = page.get("source_url") or page.get("target_url") or ""
            result["rows"].append({
                "category": category, "severity": severity, "message": name, "url": url,
            })
    return result


def get_latest_snapshot_overview(project_id: int) -> dict:
    """Latest finished snapshot's overview for a project, or a dict with an
    'error' key describing what went wrong (no snapshot yet, API units
    exhausted, etc) -- never raises, since the listing page needs to render
    a card either way and show the caller whatever SEMrush actually said."""
    try:
        snapshots = list_snapshots(project_id)
    except Exception as e:
        return {"error": str(e)}
    if not snapshots:
        return {"error": "No finished Site Audit snapshot yet"}
    try:
        return get_snapshot(project_id, snapshots[0]["snapshot_id"])
    except Exception as e:
        return {"error": str(e)}
