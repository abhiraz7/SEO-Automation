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
