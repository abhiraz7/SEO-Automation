"""
WordPress adapter tests (Task 3.3 unit coverage). HTTP is mocked -- no
network, no real WordPress site. This is the honest substitute for live
verification: there is no real claude-wp-mcp install + Bearer token
available in this environment (see AgentLog), so deploy/rollback have
never actually been exercised against a real site. What IS verified here:
token encryption round-trips correctly, and every response shape the
plugin can return (success/error/auth-failure/empty-result/unreachable)
maps to the right WordPressResult outcome.
"""
import os
from unittest.mock import MagicMock, patch

import httpx
import pytest
from cryptography.fernet import Fernet

from app import wordpress

TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def wp_token_key(monkeypatch):
    monkeypatch.setenv(wordpress.WP_TOKEN_KEY_ENV, TEST_KEY)


def test_encrypt_decrypt_round_trip():
    encrypted = wordpress.encrypt_token("raw-secret-token")
    assert encrypted != "raw-secret-token"
    assert wordpress.decrypt_token(encrypted) == "raw-secret-token"


def test_encrypt_without_key_raises(monkeypatch):
    monkeypatch.delenv(wordpress.WP_TOKEN_KEY_ENV, raising=False)
    with pytest.raises(RuntimeError, match="WP_TOKEN_KEY"):
        wordpress.encrypt_token("x")


def test_decrypt_with_wrong_key_raises(monkeypatch):
    encrypted = wordpress.encrypt_token("raw-secret-token")
    monkeypatch.setenv(wordpress.WP_TOKEN_KEY_ENV, Fernet.generate_key().decode())
    with pytest.raises(RuntimeError, match="could not be decrypted"):
        wordpress.decrypt_token(encrypted)


def _mock_response(status_code, json_body):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    return resp


def test_set_yoast_meta_success():
    with patch.object(wordpress.httpx, "post", return_value=_mock_response(
        200, {"success": True, "result": {"post_id": 42, "updated_fields": ["meta_description"]}}
    )):
        result = wordpress.set_yoast_meta("https://site.com", "tok", 42, meta_description="new desc")
    assert result.ok
    assert result.status == "ok"
    assert result.data["post_id"] == 42


def test_tool_call_reports_plugin_error():
    with patch.object(wordpress.httpx, "post", return_value=_mock_response(
        200, {"success": False, "error": "post_id required."}
    )):
        result = wordpress.set_yoast_meta("https://site.com", "tok", 0)
    assert not result.ok
    assert result.status == "error"
    assert "post_id required" in result.error


def test_tool_call_empty_result_is_no_data():
    with patch.object(wordpress.httpx, "post", return_value=_mock_response(200, {"success": True, "result": {}})):
        result = wordpress.get_yoast_meta("https://site.com", "tok", 42)
    assert result.status == "no_data"


@pytest.mark.parametrize("status_code", [401, 403])
def test_tool_call_auth_rejected(status_code):
    with patch.object(wordpress.httpx, "post", return_value=_mock_response(status_code, {})):
        result = wordpress.set_yoast_meta("https://site.com", "bad-token", 1)
    assert result.status == "error"
    assert "Authentication rejected" in result.error


def test_tool_call_server_error():
    with patch.object(wordpress.httpx, "post", return_value=_mock_response(500, {})):
        result = wordpress.set_yoast_meta("https://site.com", "tok", 1)
    assert result.status == "error"
    assert "500" in result.error


def test_tool_call_unreachable_site():
    with patch.object(wordpress.httpx, "post", side_effect=httpx.ConnectError("DNS failure")):
        result = wordpress.set_yoast_meta("https://nonexistent.invalid", "tok", 1)
    assert result.status == "error"
    assert "Could not reach" in result.error


def test_test_connection_success():
    resp = _mock_response(200, {"status": "ok", "plugin": "Claude WP Developer", "version": "1.5.2"})
    with patch.object(wordpress.httpx, "get", return_value=resp):
        result = wordpress.test_connection("https://site.com", "tok")
    assert result.ok
    assert result.data["plugin"] == "Claude WP Developer"


def test_test_connection_auth_failure():
    with patch.object(wordpress.httpx, "get", return_value=_mock_response(403, {})):
        result = wordpress.test_connection("https://site.com", "bad-token")
    assert result.status == "error"
    assert "Authentication rejected" in result.error


def test_test_connection_unreachable():
    with patch.object(wordpress.httpx, "get", side_effect=httpx.ConnectError("DNS failure")):
        result = wordpress.test_connection("https://nonexistent.invalid", "tok")
    assert result.status == "error"
    assert "Could not reach" in result.error


# ── resolve_post_id_by_url (auto WP post ID resolution, Task 3.5b) ──────

def test_resolve_post_id_matches_posts_endpoint():
    with patch.object(wordpress.httpx, "get", return_value=_mock_response(
        200, [{"id": 42, "link": "https://site.com/hello-world/"}]
    )):
        result = wordpress.resolve_post_id_by_url("https://site.com", "https://site.com/hello-world/")
    assert result.ok
    assert result.data == {"post_id": 42, "post_type": "posts"}


def test_resolve_post_id_falls_back_to_pages_endpoint():
    def fake_get(url, params=None, timeout=None):
        if url.endswith("/posts"):
            return _mock_response(200, [])
        return _mock_response(200, [{"id": 7, "link": "https://site.com/about/"}])

    with patch.object(wordpress.httpx, "get", side_effect=fake_get):
        result = wordpress.resolve_post_id_by_url("https://site.com", "https://site.com/about/")
    assert result.ok
    assert result.data == {"post_id": 7, "post_type": "pages"}


def test_resolve_post_id_no_match_is_no_data_not_error():
    with patch.object(wordpress.httpx, "get", return_value=_mock_response(200, [])):
        result = wordpress.resolve_post_id_by_url("https://site.com", "https://site.com/missing-page/")
    assert result.status == "no_data"
    assert not result.ok


def test_resolve_post_id_ambiguous_match_is_no_data():
    with patch.object(wordpress.httpx, "get", return_value=_mock_response(
        200, [{"id": 1, "link": "a"}, {"id": 2, "link": "b"}]
    )):
        result = wordpress.resolve_post_id_by_url("https://site.com", "https://site.com/dup-slug/")
    assert result.status == "no_data"


def test_resolve_post_id_homepage_has_no_slug():
    result = wordpress.resolve_post_id_by_url("https://site.com", "https://site.com/")
    assert result.status == "no_data"
    assert "slug" in result.error.lower()


def test_resolve_post_id_unreachable_is_error_not_crash():
    with patch.object(wordpress.httpx, "get", side_effect=httpx.ConnectError("timeout")):
        result = wordpress.resolve_post_id_by_url("https://nonexistent.invalid", "https://nonexistent.invalid/page/")
    assert result.status == "error"
    assert "Could not reach" in result.error


# ── Homepage resolution via get_options (Task 3.5c) ─────────────────────

def test_resolve_homepage_static_front_page():
    with patch.object(wordpress.httpx, "post", return_value=_mock_response(
        200, {"success": True, "result": {"show_on_front": "page", "page_on_front": "540"}}
    )):
        result = wordpress.resolve_post_id_by_url("https://site.com", "https://site.com/", token="tok")
    assert result.ok
    assert result.data == {"post_id": 540, "post_type": "pages"}


def test_resolve_homepage_post_archive_is_no_data_with_reason():
    with patch.object(wordpress.httpx, "post", return_value=_mock_response(
        200, {"success": True, "result": {"show_on_front": "posts", "page_on_front": "0"}}
    )):
        result = wordpress.resolve_post_id_by_url("https://site.com", "https://site.com/", token="tok")
    assert result.status == "no_data"
    assert result.data.get("reason") == "homepage_is_post_archive"


def test_resolve_homepage_without_token_skips_lookup():
    result = wordpress.resolve_post_id_by_url("https://site.com", "https://site.com/")
    assert result.status == "no_data"
    assert "no token" in result.error.lower()


def test_resolve_homepage_get_options_error_propagates():
    with patch.object(wordpress.httpx, "post", return_value=_mock_response(403, {})):
        result = wordpress.resolve_post_id_by_url("https://site.com", "https://site.com/", token="bad-tok")
    assert result.status == "error"
    assert "Authentication rejected" in result.error
