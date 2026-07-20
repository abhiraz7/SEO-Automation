"""
WordPress adapter -- talks to the claude-wp-mcp plugin's REST tool dispatcher
(POST {site_url}/wp-json/cwpm/v1/tool, Bearer auth), not the WordPress core
REST API directly. Mirrors semrush.py/dataforseo.py's role for keywords:
nothing outside this file should know the plugin's request/response shape.

Every public function returns an explicit ok/no_data/error result (see
WordPressResult below) -- same three-outcome discipline as the keyword
providers, for the same reason: a WordPress write failure must never look
like a successful no-op to the caller.

BLOCKED IN THIS ENVIRONMENT: there is no real WordPress site + plugin token
configured here, so test_connection()/set_yoast_meta() etc. are implemented
and unit-testable (mocked HTTP) but have not been verified against a live
site. That verification needs: a WordPress install with the claude-wp-mcp
plugin activated, and its site_url + Bearer token saved via
POST /projects/{id}/wordpress (Task 3.2's route, below).
"""
import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken

WP_TOKEN_KEY_ENV = "WP_TOKEN_KEY"
_TOOL_PATH = "/wp-json/cwpm/v1/tool"
_TIMEOUT = 20.0


def _get_fernet() -> Fernet:
    """WP_TOKEN_KEY must be a Fernet key (44-char urlsafe-base64 string).
    Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    and put it in .env -- see README note added alongside this module."""
    key = os.environ.get(WP_TOKEN_KEY_ENV, "").strip()
    if not key:
        raise RuntimeError(
            f"{WP_TOKEN_KEY_ENV} is not set. Generate one with: "
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" '
            "and add it to .env."
        )
    return Fernet(key.encode())


def encrypt_token(raw_token: str) -> str:
    return _get_fernet().encrypt(raw_token.encode()).decode()


def decrypt_token(encrypted_token: str) -> str:
    try:
        return _get_fernet().decrypt(encrypted_token.encode()).decode()
    except InvalidToken as e:
        raise RuntimeError("Stored WordPress token could not be decrypted -- WP_TOKEN_KEY may have changed.") from e


@dataclass
class WordPressResult:
    """Three-outcome contract, same shape/reasoning as keyword_provider's
    NormalizedKeyword.status: ok/no_data/error must stay distinguishable all
    the way to the UI, never collapsed into a fake blank success."""
    status: str  # "ok" | "no_data" | "error"
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _call_tool(site_url: str, token: str, tool: str, params: dict) -> WordPressResult:
    url = site_url.rstrip("/") + _TOOL_PATH
    try:
        resp = httpx.post(
            url,
            json={"tool": tool, "params": params},
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT,
        )
    except httpx.RequestError as e:
        return WordPressResult(status="error", error=f"Could not reach {site_url}: {e}")

    if resp.status_code == 401 or resp.status_code == 403:
        return WordPressResult(status="error", error=f"Authentication rejected (HTTP {resp.status_code}) -- check the token.")
    if resp.status_code >= 500:
        return WordPressResult(status="error", error=f"WordPress site error (HTTP {resp.status_code})")

    try:
        body = resp.json()
    except ValueError:
        return WordPressResult(status="error", error=f"Non-JSON response (HTTP {resp.status_code})")

    if not body.get("success"):
        return WordPressResult(status="error", error=body.get("error") or f"Tool {tool!r} failed (HTTP {resp.status_code})")

    result = body.get("result") or {}
    if not result:
        return WordPressResult(status="no_data", data={})
    return WordPressResult(status="ok", data=result)


def _resolve_homepage_post_id(site_url: str, token: str) -> WordPressResult:
    """Root/homepage URLs have no slug, so the core REST slug lookup
    (below) can't apply. Instead ask the plugin (via get_options, a
    theme-group tool that reads wp_options) which post is set as the
    static front page. show_on_front/page_on_front are WordPress's own
    settings under Settings > Reading -- not our data, so this is exactly
    as reliable as WordPress itself is about its own homepage.

    If the homepage shows the latest-posts blog roll instead of a single
    static page (show_on_front == 'posts'), there IS no single post/page
    to deploy a title/meta fix to -- that's flagged as
    reason='homepage_is_post_archive' so the caller can show an honest
    explanation instead of asking for a numeric ID that wouldn't help.
    """
    result = _call_tool(site_url, token, "get_options", {"keys": ["show_on_front", "page_on_front"]})
    if result.status == "error":
        return result
    options = result.data or {}
    show_on_front = options.get("show_on_front")
    page_on_front = options.get("page_on_front")

    if show_on_front == "page":
        try:
            post_id = int(page_on_front)
        except (TypeError, ValueError):
            post_id = 0
        if post_id > 0:
            return WordPressResult(status="ok", data={"post_id": post_id, "post_type": "pages"})

    return WordPressResult(
        status="no_data",
        error="This site's homepage displays the latest posts (a blog roll), not a single WordPress page -- there's no one post/page to deploy a homepage fix to.",
        data={"reason": "homepage_is_post_archive"},
    )


def resolve_post_id_by_url(site_url: str, page_url: str, token: str | None = None) -> WordPressResult:
    """Best-effort lookup of a page's WordPress post ID from its live URL.

    For normal pages/posts: uses WordPress's own public core REST API
    (wp-json/wp/v2), NOT the claude-wp-mcp plugin -- the plugin exposes no
    URL/slug lookup tool (see module docstring). Needs no token for this
    path: the core API's slug lookup is public for published content on
    virtually every WordPress site.

    For the homepage/root URL (no slug to look up): falls back to
    _resolve_homepage_post_id, which DOES need a token (it's a plugin
    tool call). If no token is supplied, homepage resolution is skipped
    and this returns no_data, same as before token support existed.

    Never raises. A slow/offline site, a missing REST API, or an
    ambiguous/missing slug all just mean resolution didn't happen --
    callers (crawl, deploy) must treat that as normal and fall back to
    asking the user for the ID manually, not as something to crash or
    block on.

    Tries /posts then /pages (the same slug can exist under either post
    type); only trusts a single unambiguous match.
    """
    path = urlparse(page_url).path.strip("/")
    slug = path.rsplit("/", 1)[-1] if path else ""
    if not slug:
        if token:
            return _resolve_homepage_post_id(site_url, token)
        return WordPressResult(status="no_data", error="Homepage/root URLs have no slug to resolve (no token supplied for a get_options lookup)")

    base = site_url.rstrip("/")
    for post_type in ("posts", "pages"):
        try:
            resp = httpx.get(
                f"{base}/wp-json/wp/v2/{post_type}",
                params={"slug": slug, "_fields": "id,link"},
                timeout=_TIMEOUT,
            )
        except httpx.RequestError as e:
            return WordPressResult(status="error", error=f"Could not reach {site_url}: {e}")
        if resp.status_code != 200:
            continue
        try:
            results = resp.json()
        except ValueError:
            continue
        if isinstance(results, list) and len(results) == 1 and "id" in results[0]:
            return WordPressResult(status="ok", data={"post_id": results[0]["id"], "post_type": post_type})

    return WordPressResult(status="no_data", error=f"No unique post/page found for slug {slug!r}")


def test_connection(site_url: str, token: str) -> WordPressResult:
    """Hits the plugin's /ping REST route (not the generic /tool dispatcher --
    ping is a plain GET, no tool call semantics)."""
    url = site_url.rstrip("/") + "/wp-json/cwpm/v1/ping"
    try:
        resp = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT)
    except httpx.RequestError as e:
        return WordPressResult(status="error", error=f"Could not reach {site_url}: {e}")
    if resp.status_code == 401 or resp.status_code == 403:
        return WordPressResult(status="error", error=f"Authentication rejected (HTTP {resp.status_code}) -- check the token.")
    if resp.status_code != 200:
        return WordPressResult(status="error", error=f"Unexpected response (HTTP {resp.status_code})")
    try:
        return WordPressResult(status="ok", data=resp.json())
    except ValueError:
        return WordPressResult(status="error", error="Ping succeeded but response wasn't valid JSON.")


def set_yoast_meta(site_url: str, token: str, post_id: int, **fields) -> WordPressResult:
    """fields: any of seo_title, meta_description, focus_keyword, canonical_url,
    og_title, og_description, is_cornerstone, schema_page_type, ... (see the
    plugin's yoast_set_meta tool). Only the keys actually passed are updated
    on the WordPress side -- mirrors the plugin's own partial-update contract."""
    return _call_tool(site_url, token, "yoast_set_meta", {"post_id": post_id, **fields})


def get_yoast_meta(site_url: str, token: str, post_id: int) -> WordPressResult:
    return _call_tool(site_url, token, "yoast_get_meta", {"post_id": post_id})


def update_post_content(site_url: str, token: str, post_id: int, **fields) -> WordPressResult:
    """fields: any of title, content, excerpt, slug, status, meta (dict) --
    see the plugin's update_post tool. Used for H1/content-level fixes."""
    return _call_tool(site_url, token, "update_post", {"post_id": post_id, **fields})


def update_media_alt_text(site_url: str, token: str, media_id: int, alt: str) -> WordPressResult:
    return _call_tool(site_url, token, "update_media_meta", {"media_id": media_id, "alt": alt})


def get_post(site_url: str, token: str, post_id: int) -> WordPressResult:
    """Used to read the CURRENT value of a field before deploying, so
    SuggestionRevision.before_value is the real prior value, not an assumption."""
    return _call_tool(site_url, token, "get_post", {"post_id": post_id})
