import pytest

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
