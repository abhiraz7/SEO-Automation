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
