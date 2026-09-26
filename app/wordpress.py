"""
WordPress adapter -- talks to the AI SEO Connector plugin's REST tool dispatcher
(POST {site_url}/wp-json/vtseo/v1/tool, Bearer auth), not the WordPress core
REST API directly. Mirrors semrush.py/dataforseo.py's role for keywords:
nothing outside this file should know the plugin's request/response shape.

AI SEO Connector (formerly VtechSEO Agent) replaced the earlier general-purpose claude-wp-mcp dev plugin
for this connection (different REST namespace: vtseo/v1, not cwpm/v1) --
any site still running only claude-wp-mcp will fail every call here with a
connection/404-style error until it installs the new plugin from
/downloads/ai-seo-connector and reconnects. The plugin still answers on the legacy
vtseo/v1 route this file calls, so that alias must stay.

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
_TOOL_PATH = "/wp-json/vtseo/v1/tool"
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


def _rest_post_type_bases(site_url: str) -> list[str]:
    """REST rest_base values for every post type this site exposes over
    wp/v2/types, "posts" and "pages" first (cheapest -- covers the vast
    majority of sites in one or two requests before falling through to
    custom post types like a theme's "free_notes" or "case_studies").

    Sites without custom post types, or whose /types endpoint is
    blocked/unreachable, just get the two built-ins back -- same
    behavior as before this existed. Never raises."""
    bases = ["posts", "pages"]
    try:
        resp = httpx.get(f"{site_url.rstrip('/')}/wp-json/wp/v2/types", timeout=_TIMEOUT)
        if resp.status_code != 200:
            return bases
        types = resp.json()
    except (httpx.RequestError, ValueError):
        return bases
    if not isinstance(types, dict):
        return bases

    extra = sorted({
        t.get("rest_base") for t in types.values()
        if isinstance(t, dict) and t.get("rest_base") and t.get("rest_base") not in bases
        # media/menu-items/blocks/templates/etc. are WordPress-internal REST
        # types, never a page a suggestion could target -- skip them so a
        # broken/large site doesn't turn every deploy into N extra requests.
        and t.get("rest_base") not in ("media", "menu-items", "blocks", "templates", "template-parts",
                                        "global-styles", "navigation", "font-families")
    })
    return bases + extra


def _norm_path(url) -> str:
    """Path of a URL with leading/trailing/duplicate edge slashes removed, so
    'https://x.com//a/b/' and 'https://x.com/a/b' compare equal."""
    return urlparse(str(url or "")).path.strip("/")


# URL shapes that are real pages on the site but have NO single WordPress
# post/term behind them, so there is nothing to deploy a title or description
# to. Each maps to a plain-language reason the UI can show instead of asking
# the user for a numeric ID that could never exist.
_NON_DEPLOYABLE_MESSAGES = {
    "query_url": "This URL has a query string (a filter or search view of another page). It isn't a page of its own in WordPress, so there's nothing to deploy to. Deploy to the page it filters instead.",
    "pagination": "This is a paginated archive view (page 2, 3, ...). It has no post or term of its own to deploy a title or description to.",
    "media_file": "This is an uploaded file (image, PDF...), not a WordPress page. Fix image alt text through the image tools instead.",
    "infrastructure": "This is a server/CDN utility URL, not a WordPress page.",
    "feed": "This is an RSS feed, not a WordPress page.",
}


def non_deployable_reason(page_url: str) -> str | None:
    """Reason key (see _NON_DEPLOYABLE_MESSAGES) if this URL can never be
    deployed to, else None. Pure string checks -- no network."""
    parsed = urlparse(str(page_url or ""))
    path = parsed.path.strip("/")
    if parsed.query:
        return "query_url"
    if path.startswith("wp-content/") or path.startswith("wp-includes/"):
        return "media_file"
    if path.startswith("cdn-cgi/"):
        return "infrastructure"
    segments = path.split("/") if path else []
    if len(segments) >= 2 and segments[-2] == "page" and segments[-1].isdigit():
        return "pagination"
    if segments and segments[-1] == "feed":
        return "feed"
    return None


def non_deployable_message(reason: str) -> str:
    return _NON_DEPLOYABLE_MESSAGES.get(reason, "This URL can't be deployed to.")


# Taxonomies that are WordPress internals, never a page a visitor lands on.
_INTERNAL_TAXONOMIES = {"nav_menu", "wp_pattern_category", "link_category"}


def resolve_term_by_url(site_url: str, page_url: str) -> WordPressResult:
    """Best-effort lookup of a taxonomy TERM (category, tag, custom taxonomy
    archive such as /subject/hindi/) from its live URL, via core REST
    (wp/v2/taxonomies, then wp/v2/<rest_base>?slug=...). Public, needs no token.

    Only trusts a term whose own `link` has exactly this page's path, so a term
    that merely shares a slug with the page (or with a term in another taxonomy)
    is never picked. Returns data={"taxonomy", "term_id"} on success. Never
    raises: a missing/blocked REST API just means no_data."""
    slug = _norm_path(page_url).rsplit("/", 1)[-1]
    if not slug:
        return WordPressResult(status="no_data", error="No slug to look up.")
    base = site_url.rstrip("/")
    try:
        resp = httpx.get(f"{base}/wp-json/wp/v2/taxonomies", timeout=_TIMEOUT)
        if resp.status_code != 200:
            return WordPressResult(status="no_data", error="Taxonomies endpoint not available.")
        taxonomies = resp.json()
    except (httpx.RequestError, ValueError):
        return WordPressResult(status="no_data", error="Could not read the site's taxonomies.")
    if not isinstance(taxonomies, dict):
        return WordPressResult(status="no_data", error="Unexpected taxonomies response.")

    wanted = _norm_path(page_url)
    found = []
    for name, tax in sorted(taxonomies.items()):
        if name in _INTERNAL_TAXONOMIES or not isinstance(tax, dict) or not tax.get("rest_base"):
            continue
        try:
            r = httpx.get(
                f"{base}/wp-json/wp/v2/{tax['rest_base']}",
                params={"slug": slug, "_fields": "id,link,taxonomy"},
                timeout=_TIMEOUT,
            )
        except httpx.RequestError:
            return WordPressResult(status="error", error=f"Could not reach {site_url}")
        if r.status_code != 200:
            continue
        try:
            terms = r.json()
        except ValueError:
            continue
        for t in terms if isinstance(terms, list) else []:
            if isinstance(t, dict) and "id" in t and _norm_path(t.get("link")) == wanted:
                found.append({"taxonomy": t.get("taxonomy") or name, "term_id": t["id"]})
    if len(found) == 1:
        return WordPressResult(status="ok", data=found[0])
    return WordPressResult(status="no_data", error=f"No unique taxonomy term found for {slug!r}")



def resolve_post_id_by_url(site_url: str, page_url: str, token: str | None = None) -> WordPressResult:
    """Best-effort lookup of a page's WordPress post ID from its live URL.

    For normal pages/posts (and any custom post type the site registers
    with show_in_rest=True -- see _rest_post_type_bases): uses WordPress's
    own public core REST API (wp-json/wp/v2), NOT the claude-wp-mcp plugin
    -- the plugin exposes no URL/slug lookup tool (see module docstring).
    Needs no token for this path: the core API's slug lookup is public for
    published content on virtually every WordPress site.

    For the homepage/root URL (no slug to look up): falls back to
    _resolve_homepage_post_id, which DOES need a token (it's a plugin
    tool call). If no token is supplied, homepage resolution is skipped
    and this returns no_data, same as before token support existed.

    Never raises. A slow/offline site, a missing REST API, or an
    ambiguous/missing slug all just mean resolution didn't happen --
    callers (crawl, deploy) must treat that as normal and fall back to
    asking the user for the ID manually, not as something to crash or
    block on.

    Tries every REST-exposed post type's rest_base in turn (the same slug
    can exist under more than one); only trusts a single unambiguous
    match under whichever type responds first.
    """
    path = urlparse(page_url).path.strip("/")
    slug = path.rsplit("/", 1)[-1] if path else ""
    if not slug:
        if token:
            return _resolve_homepage_post_id(site_url, token)
        return WordPressResult(status="no_data", error="Homepage/root URLs have no slug to resolve (no token supplied for a get_options lookup)")

    base = site_url.rstrip("/")
    for post_type in _rest_post_type_bases(site_url):
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
        if isinstance(results, list):
            matches = [r for r in results if isinstance(r, dict) and "id" in r]
            if len(matches) == 1:
                return WordPressResult(status="ok", data={"post_id": matches[0]["id"], "post_type": post_type})
            if len(matches) > 1:
                # Same slug under different parents (e.g. /buy-backlinks/premium-plan/
                # and /google-stacking/premium-plan/): the REST response carries each
                # item's real link, so pick the one whose path is exactly this page's.
                exact = [r for r in matches if _norm_path(r.get("link")) == _norm_path(page_url)]
                if len(exact) == 1:
                    return WordPressResult(status="ok", data={"post_id": exact[0]["id"], "post_type": post_type})

    return WordPressResult(status="no_data", error=f"No unique post/page found for slug {slug!r}")


def test_connection(site_url: str, token: str) -> WordPressResult:
    """Hits the plugin's /ping REST route (not the generic /tool dispatcher --
    ping is a plain GET, no tool call semantics)."""
    url = site_url.rstrip("/") + "/wp-json/vtseo/v1/ping"
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


def update_media_alt_by_url(site_url: str, token: str, image_url: str, alt: str) -> WordPressResult:
    """For images with no known media_id (see html_extract._wp_media_id --
    theme-level images like a logo carry no wp-image-N class to read one
    from). Calls the plugin's update_media_alt_by_url tool, which resolves
    image_url to a real attachment via WordPress's own
    attachment_url_to_postid() before writing. Fails as a normal 'error'
    WordPressResult (not an exception) when the image isn't in this site's
    own media library -- hotlinked/CDN images genuinely can't be fixed
    this way, and the caller needs that surfaced, not swallowed."""
    return _call_tool(site_url, token, "update_media_alt_by_url", {"url": image_url, "alt": alt})


def get_post(site_url: str, token: str, post_id: int) -> WordPressResult:
    """Used to read the CURRENT value of a field before deploying, so
    SuggestionRevision.before_value is the real prior value, not an assumption."""
    return _call_tool(site_url, token, "get_post", {"post_id": post_id})


def get_term_seo(site_url: str, token: str, taxonomy: str, term_id: int) -> WordPressResult:
    """Reads a taxonomy term's SEO title/description/focus keyword via the
    plugin's seo_get_term_meta tool (plugin 1.6.0+, RankMath only)."""
    return _call_tool(site_url, token, "seo_get_term_meta", {"taxonomy": taxonomy, "term_id": term_id})


def set_term_seo(site_url: str, token: str, taxonomy: str, term_id: int, **fields) -> WordPressResult:
    """fields: any of seo_title, meta_description, focus_keyword. An empty
    string removes the custom value (the plugin deletes the term meta), which is
    also what rolling back to an empty before-value needs."""
    return _call_tool(site_url, token, "seo_set_term_meta", {"taxonomy": taxonomy, "term_id": term_id, **fields})
