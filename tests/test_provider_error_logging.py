"""Provider clients used to swallow errors into return values, so nothing reached
the log and the Settings error viewer had nothing to show. They now log once at
the shared request helper -- with the endpoint and reason, never a key or URL."""
import logging
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from app import dataforseo, dataforseo_onpage, semrush
from app.redact import redact


def _messages(caplog):
    return [r.getMessage() for r in caplog.records]


def test_dataforseo_onpage_logs_an_api_level_error_inside_a_200(monkeypatch, caplog):
    monkeypatch.setenv("DATAFORSEO_LOGIN", "user@example.com")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "pw-pw-pw-pw")
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"status_code": 40210, "status_message": "Payment required. Insufficient funds."}
    with patch("app.dataforseo_onpage.httpx.post", return_value=resp), caplog.at_level(logging.WARNING):
        out = dataforseo_onpage._post("/on_page/instant_pages", [{"url": "https://x.com"}])
    assert out == {"error": "Payment required. Insufficient funds."}
    line = next(m for m in _messages(caplog) if "dataforseo_onpage.api_error" in m)
    assert "path=/on_page/instant_pages" in line and "40210" in line and "Insufficient funds" in line
    assert "pw-pw-pw-pw" not in line


def test_dataforseo_onpage_logs_a_network_failure(monkeypatch, caplog):
    monkeypatch.setenv("DATAFORSEO_LOGIN", "user@example.com")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "pw-pw-pw-pw")
    with patch("app.dataforseo_onpage.httpx.post", side_effect=TimeoutError("read timed out")), caplog.at_level(logging.WARNING):
        out = dataforseo_onpage._post("/on_page/task_post", [])
    assert "error" in out
    assert any("dataforseo_onpage.request_failed" in m and "TimeoutError" in m for m in _messages(caplog))


def test_dataforseo_keyword_client_logs_http_and_api_errors(monkeypatch, caplog):
    monkeypatch.setenv("DATAFORSEO_LOGIN", "user@example.com")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "pw-pw-pw-pw")
    bad = MagicMock()
    bad.raise_for_status.side_effect = RuntimeError("Client error '401 Unauthorized'")
    with patch("app.dataforseo.httpx.post", return_value=bad), caplog.at_level(logging.WARNING), pytest.raises(RuntimeError):
        dataforseo._post("/serp/google/organic/live/advanced", [])
    assert any("dataforseo.request_failed" in m and "401" in m for m in _messages(caplog))

    caplog.clear()
    ok_http = MagicMock()
    ok_http.json.return_value = {"status_code": 40104, "status_message": "Please verify your email"}
    with patch("app.dataforseo.httpx.post", return_value=ok_http), caplog.at_level(logging.WARNING):
        assert dataforseo._post("/dataforseo_labs/x", [])["status_code"] == 40104   # return value unchanged
    assert any("dataforseo.api_error" in m and "40104" in m for m in _messages(caplog))


def test_semrush_logs_a_failed_request_without_the_key_url(monkeypatch, caplog):
    secret_url = "https://api.semrush.com/?type=phrase_this&key=ffffffffffffffffffffffffffffffff&phrase=x"
    err = urllib.error.HTTPError(secret_url, 403, "Forbidden", {}, None)
    with patch("app.semrush.urllib.request.urlopen", side_effect=err), caplog.at_level(logging.WARNING), pytest.raises(urllib.error.HTTPError):
        semrush._get(secret_url)
    line = next(m for m in _messages(caplog) if "semrush.request_failed" in m)
    assert "HTTPError" in line and "403" in line
    assert "ffffffff" not in redact(line, {})     # and the stored copy is redacted regardless
