from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from app import google_search_console as gsc

_ALL_VARS = {
    "GOOGLE_OAUTH_CLIENT_ID": "client-id",
    "GOOGLE_OAUTH_CLIENT_SECRET": "client-secret",
    "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:8000/gsc/callback",
    "GSC_TOKEN_KEY": "some-fernet-key",
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every GSC env var starts unset for each test, regardless of what the
    real environment (e.g. a developer's own .env) has loaded."""
    for name in _ALL_VARS:
        monkeypatch.delenv(name, raising=False)


def test_is_configured_true_when_all_vars_set(monkeypatch):
    for name, value in _ALL_VARS.items():
        monkeypatch.setenv(name, value)
    assert gsc.is_configured() is True


@pytest.mark.parametrize("missing", list(_ALL_VARS))
def test_is_configured_false_when_one_var_missing(monkeypatch, missing):
    for name, value in _ALL_VARS.items():
        if name != missing:
            monkeypatch.setenv(name, value)
    assert gsc.is_configured() is False


@pytest.mark.parametrize("missing", list(_ALL_VARS))
def test_is_configured_false_when_one_var_blank(monkeypatch, missing):
    for name, value in _ALL_VARS.items():
        monkeypatch.setenv(name, "" if name == missing else value)
    assert gsc.is_configured() is False


def test_is_configured_false_when_var_is_only_whitespace(monkeypatch):
    for name, value in _ALL_VARS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("GSC_TOKEN_KEY", "   ")
    assert gsc.is_configured() is False


def test_is_configured_false_when_nothing_set():
    assert gsc.is_configured() is False


# ── Token encryption (mirrors app/wordpress.py's tests exactly) ──────────

TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture
def gsc_token_key(monkeypatch):
    monkeypatch.setenv(gsc._ENV_TOKEN_KEY, TEST_KEY)


def test_encrypt_decrypt_round_trip(gsc_token_key):
    encrypted = gsc.encrypt_token("raw-refresh-token")
    assert encrypted != "raw-refresh-token"
    assert gsc.decrypt_token(encrypted) == "raw-refresh-token"


def test_encrypt_without_key_raises():
    with pytest.raises(RuntimeError, match="GSC_TOKEN_KEY"):
        gsc.encrypt_token("x")


def test_decrypt_with_wrong_key_raises(gsc_token_key):
    encrypted = gsc.encrypt_token("raw-refresh-token")
    os_environ_key = gsc._ENV_TOKEN_KEY
    import os
    os.environ[os_environ_key] = Fernet.generate_key().decode()
    with pytest.raises(RuntimeError, match="could not be decrypted"):
        gsc.decrypt_token(encrypted)


# ── OAuth helpers ──────────────────────────────────────────────────────
# _ALL_VARS' GSC_TOKEN_KEY placeholder ("some-fernet-key") is not a valid
# Fernet key -- fine here since none of these tests call encrypt/decrypt,
# only _client_config()/Credentials(), which just read the env vars as
# plain strings.

def _set_oauth_env(monkeypatch):
    for name, value in _ALL_VARS.items():
        monkeypatch.setenv(name, value)


def _fake_creds(token="new-access-token", refresh_token="a-refresh-token", expiry=None, scopes=None):
    creds = MagicMock()
    creds.token = token
    creds.refresh_token = refresh_token
    creds.expiry = expiry or datetime(2026, 1, 1, tzinfo=timezone.utc)
    creds.scopes = scopes or gsc.SCOPES
    return creds


def test_build_auth_url_includes_offline_and_consent(monkeypatch):
    _set_oauth_env(monkeypatch)
    fake_flow = MagicMock()
    fake_flow.authorization_url.return_value = ("https://accounts.google.com/o/oauth2/auth?state=abc", "abc")
    with patch.object(gsc.Flow, "from_client_config", return_value=fake_flow):
        url = gsc.build_auth_url("signed-state-value")
    assert url.startswith("https://accounts.google.com")
    _, kwargs = fake_flow.authorization_url.call_args
    assert kwargs["access_type"] == "offline"
    assert kwargs["prompt"] == "consent"


def test_exchange_code_for_tokens_success(monkeypatch):
    _set_oauth_env(monkeypatch)
    fake_flow = MagicMock()
    fake_flow.credentials = _fake_creds()
    with patch.object(gsc.Flow, "from_client_config", return_value=fake_flow), \
         patch.object(gsc, "_account_email", return_value="agency@example.com"):
        result = gsc.exchange_code_for_tokens("auth-code")
    assert result.ok
    assert result.data["google_account_email"] == "agency@example.com"
    assert result.data["refresh_token"] == "a-refresh-token"
    assert result.data["access_token"] == "new-access-token"


def test_exchange_code_for_tokens_missing_refresh_token_is_error(monkeypatch):
    _set_oauth_env(monkeypatch)
    fake_flow = MagicMock()
    fake_flow.credentials = _fake_creds(refresh_token=None)
    with patch.object(gsc.Flow, "from_client_config", return_value=fake_flow):
        result = gsc.exchange_code_for_tokens("auth-code")
    assert result.status == "error"
    assert "refresh token" in result.error.lower()


def test_exchange_code_for_tokens_cannot_resolve_email_is_error(monkeypatch):
    _set_oauth_env(monkeypatch)
    fake_flow = MagicMock()
    fake_flow.credentials = _fake_creds()
    with patch.object(gsc.Flow, "from_client_config", return_value=fake_flow), \
         patch.object(gsc, "_account_email", return_value=None):
        result = gsc.exchange_code_for_tokens("auth-code")
    assert result.status == "error"
    assert "email" in result.error.lower()


def test_exchange_code_for_tokens_fetch_token_raises_is_error(monkeypatch):
    _set_oauth_env(monkeypatch)
    fake_flow = MagicMock()
    fake_flow.fetch_token.side_effect = Exception("invalid_grant: bad code")
    with patch.object(gsc.Flow, "from_client_config", return_value=fake_flow):
        result = gsc.exchange_code_for_tokens("bad-code")
    assert result.status == "error"
    assert "bad code" in result.error


def test_refresh_access_token_success(monkeypatch):
    _set_oauth_env(monkeypatch)
    def _do_refresh(request):
        creds.token = "refreshed-access-token"

    creds = _fake_creds()
    creds.refresh.side_effect = _do_refresh
    with patch.object(gsc, "Credentials", return_value=creds):
        result = gsc.refresh_access_token("a-refresh-token")
    assert result.ok
    assert result.data["access_token"] == "refreshed-access-token"


def test_refresh_access_token_invalid_grant_marks_revoked(monkeypatch):
    _set_oauth_env(monkeypatch)
    creds = _fake_creds()
    creds.refresh.side_effect = RefreshError("invalid_grant: Token has been expired or revoked.")
    with patch.object(gsc, "Credentials", return_value=creds):
        result = gsc.refresh_access_token("a-refresh-token")
    assert result.status == "error"
    assert result.data["revoked"] is True


def test_refresh_access_token_other_refresh_error_not_marked_revoked(monkeypatch):
    _set_oauth_env(monkeypatch)
    creds = _fake_creds()
    creds.refresh.side_effect = RefreshError("temporary network issue")
    with patch.object(gsc, "Credentials", return_value=creds):
        result = gsc.refresh_access_token("a-refresh-token")
    assert result.status == "error"
    assert result.data["revoked"] is False


# ── list_sites ─────────────────────────────────────────────────────────

def _fake_http_error(status_code, reason="Forbidden"):
    resp = MagicMock()
    resp.status = status_code
    resp.reason = reason
    return HttpError(resp, b'{"error": {"message": "denied"}}')


def test_list_sites_ok_maps_site_entry_array(monkeypatch):
    _set_oauth_env(monkeypatch)
    fake_creds = _fake_creds()
    fake_service = MagicMock()
    fake_service.sites.return_value.list.return_value.execute.return_value = {
        "siteEntry": [
            {"siteUrl": "https://vtechys.com/", "permissionLevel": "siteOwner"},
            {"siteUrl": "sc-domain:examnotespdf.in", "permissionLevel": "siteFullUser"},
        ]
    }
    with patch.object(gsc, "Credentials", return_value=fake_creds), \
         patch.object(gsc, "build", return_value=fake_service):
        result = gsc.list_sites("access-tok", "refresh-tok")
    assert result.ok
    assert len(result.data["sites"]) == 2
    assert result.data["sites"][0] == {"site_url": "https://vtechys.com/", "permission_level": "siteOwner"}
    # credentials snapshot for re-persisting, per Task 3's "re-persist after every call" note
    assert result.data["access_token"] == fake_creds.token


def test_list_sites_no_data_when_site_entry_missing(monkeypatch):
    _set_oauth_env(monkeypatch)
    fake_creds = _fake_creds()
    fake_service = MagicMock()
    fake_service.sites.return_value.list.return_value.execute.return_value = {}
    with patch.object(gsc, "Credentials", return_value=fake_creds), \
         patch.object(gsc, "build", return_value=fake_service):
        result = gsc.list_sites("access-tok", "refresh-tok")
    assert result.status == "no_data"
    assert result.data["sites"] == []


def test_list_sites_no_data_when_site_entry_empty_array(monkeypatch):
    _set_oauth_env(monkeypatch)
    fake_creds = _fake_creds()
    fake_service = MagicMock()
    fake_service.sites.return_value.list.return_value.execute.return_value = {"siteEntry": []}
    with patch.object(gsc, "Credentials", return_value=fake_creds), \
         patch.object(gsc, "build", return_value=fake_service):
        result = gsc.list_sites("access-tok", "refresh-tok")
    assert result.status == "no_data"


def test_list_sites_http_error_is_error(monkeypatch):
    _set_oauth_env(monkeypatch)
    fake_creds = _fake_creds()
    fake_service = MagicMock()
    fake_service.sites.return_value.list.return_value.execute.side_effect = _fake_http_error(403)
    with patch.object(gsc, "Credentials", return_value=fake_creds), \
         patch.object(gsc, "build", return_value=fake_service):
        result = gsc.list_sites("access-tok", "refresh-tok")
    assert result.status == "error"
    assert "403" in result.error


def test_list_sites_invalid_grant_marks_revoked(monkeypatch):
    _set_oauth_env(monkeypatch)
    fake_creds = _fake_creds()
    fake_service = MagicMock()
    fake_service.sites.return_value.list.return_value.execute.side_effect = RefreshError(
        "invalid_grant: Token has been expired or revoked."
    )
    with patch.object(gsc, "Credentials", return_value=fake_creds), \
         patch.object(gsc, "build", return_value=fake_service):
        result = gsc.list_sites("access-tok", "refresh-tok")
    assert result.status == "error"
    assert result.data["revoked"] is True
