"""
Plugin download route: the file must be served as ai-seo-connector-<version>.zip
(never the old vtechseo-agent name), and must still work -- via the plain
redirect -- when GitHub can't be reached. No network: httpx.get is mocked.
"""
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.routes import wordpress as wp_routes


@pytest.fixture(autouse=True)
def _clear_release_cache():
    wp_routes._plugin_release_cache.update(at=0.0, value=None)
    yield
    wp_routes._plugin_release_cache.update(at=0.0, value=None)


def _resp(json_data=None, content=b"", status=200):
    r = MagicMock()
    r.json.return_value = json_data
    r.content = content
    r.status_code = status
    r.raise_for_status.side_effect = None if status < 400 else httpx.HTTPStatusError("bad", request=MagicMock(), response=MagicMock())
    return r


def _release(tag="v1.5.0", asset="ai-seo-connector.zip"):
    return {"tag_name": tag, "assets": [{"name": asset, "browser_download_url": f"https://example.test/{asset}"}]}


def test_download_is_named_with_plugin_name_and_version():
    with patch.object(wp_routes.httpx, "get", side_effect=[_resp(_release("v1.5.0")), _resp(content=b"PKzipbytes")]):
        resp = wp_routes.download_wp_plugin()
    assert resp.headers["content-disposition"] == 'attachment; filename="ai-seo-connector-1.5.0.zip"'
    assert resp.media_type == "application/zip"
    assert resp.body == b"PKzipbytes"


def test_old_brand_name_never_appears_in_filename():
    with patch.object(wp_routes.httpx, "get", side_effect=[_resp(_release("v1.6.0")), _resp(content=b"x")]):
        resp = wp_routes.download_wp_plugin()
    assert "vtechseo" not in resp.headers["content-disposition"].lower()


def test_falls_back_to_redirect_when_github_api_unreachable():
    with patch.object(wp_routes.httpx, "get", side_effect=httpx.ConnectError("no network")):
        resp = wp_routes.download_wp_plugin()
    assert resp.status_code == 302
    assert resp.headers["location"] == wp_routes.PLUGIN_DOWNLOAD_URL


def test_falls_back_to_redirect_when_zip_download_fails():
    with patch.object(wp_routes.httpx, "get", side_effect=[_resp(_release()), httpx.ConnectError("boom")]):
        resp = wp_routes.download_wp_plugin()
    assert resp.status_code == 302


def test_falls_back_when_release_has_no_matching_asset():
    with patch.object(wp_routes.httpx, "get", return_value=_resp(_release(asset="something-else.zip"))):
        resp = wp_routes.download_wp_plugin()
    assert resp.status_code == 302


@pytest.mark.parametrize("bad_tag", ['v1.5.0"; evil=1', "latest", "", "v1.5", "1.5.0\r\nX-Injected: 1"])
def test_unsafe_or_odd_tag_is_never_put_in_the_header(bad_tag):
    with patch.object(wp_routes.httpx, "get", return_value=_resp(_release(bad_tag))):
        resp = wp_routes.download_wp_plugin()
    assert resp.status_code == 302  # fell back instead of building a header from it


def test_release_lookup_is_cached_between_calls():
    with patch.object(wp_routes.httpx, "get", side_effect=[_resp(_release()), _resp(content=b"a"), _resp(content=b"b")]) as mock_get:
        wp_routes.download_wp_plugin()
        wp_routes.download_wp_plugin()
    # 1 API lookup + 2 zip downloads; the second call did NOT hit the GitHub API again.
    assert mock_get.call_count == 3
