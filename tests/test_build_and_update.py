"""
/version (build_info) and the plugin "update available" check. No network:
GitHub is mocked, git/env/file are controlled per test.
"""
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app import build_info
from app.routes import wordpress as wp_routes

GOOD = "a" * 40


# ── build_info ───────────────────────────────────────────────────────────

def test_env_wins_over_everything(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_COMMIT", GOOD)
    monkeypatch.setattr(build_info, "_COMMIT_FILE", tmp_path / "nope")
    assert build_info.get_build_info() == {"commit": GOOD, "source": "env"}


def test_file_used_when_no_env(monkeypatch, tmp_path):
    monkeypatch.delenv("APP_COMMIT", raising=False)
    f = tmp_path / ".app_commit"
    f.write_text("b" * 40 + "\n")
    monkeypatch.setattr(build_info, "_COMMIT_FILE", f)
    assert build_info.get_build_info() == {"commit": "b" * 40, "source": "file"}


def test_git_used_when_no_env_or_file(monkeypatch, tmp_path):
    monkeypatch.delenv("APP_COMMIT", raising=False)
    monkeypatch.setattr(build_info, "_COMMIT_FILE", tmp_path / "missing")
    fake = MagicMock(returncode=0, stdout="c" * 40 + "\n")
    with patch.object(build_info.subprocess, "run", return_value=fake):
        assert build_info.get_build_info() == {"commit": "c" * 40, "source": "git"}


def test_unknown_not_an_error_when_nothing_available(monkeypatch, tmp_path):
    monkeypatch.delenv("APP_COMMIT", raising=False)
    monkeypatch.setattr(build_info, "_COMMIT_FILE", tmp_path / "missing")
    with patch.object(build_info.subprocess, "run", side_effect=FileNotFoundError("no git")):
        assert build_info.get_build_info() == {"commit": "unknown", "source": "none"}


@pytest.mark.parametrize("bad", ["<script>alert(1)</script>", "not-a-hash", "abc", "g" * 40, "a" * 41, ""])
def test_value_that_is_not_a_git_hash_is_never_echoed(monkeypatch, tmp_path, bad):
    monkeypatch.setenv("APP_COMMIT", bad)
    monkeypatch.setattr(build_info, "_COMMIT_FILE", tmp_path / "missing")
    with patch.object(build_info.subprocess, "run", side_effect=FileNotFoundError):
        info = build_info.get_build_info()
    assert info["commit"] == "unknown"


def test_version_endpoint_returns_build_info(monkeypatch):
    monkeypatch.setenv("APP_COMMIT", GOOD)
    from app.main import app
    resp = TestClient(app).get("/version")
    assert resp.status_code == 200
    assert resp.json() == {"commit": GOOD, "source": "env"}


# ── plugin update check ──────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_cache():
    wp_routes._plugin_release_cache.update(at=0.0, value=None)
    yield
    wp_routes._plugin_release_cache.update(at=0.0, value=None)


def _latest(version):
    return patch.object(wp_routes, "_latest_plugin_release", return_value=(version, "https://example.test/x.zip"))


@pytest.mark.parametrize("installed,latest,expected", [
    ("1.4.0", "1.5.0", True),    # older
    ("1.5.0", "1.5.0", False),   # same
    ("1.6.0", "1.5.0", False),   # newer than the published release (e.g. installed by hand)
    ("1.9.0", "1.10.0", True),   # numeric, not string, comparison
    ("1.10.0", "1.9.0", False),  # a string compare would get this wrong
])
def test_update_available_compares_numerically(installed, latest, expected):
    with _latest(latest):
        assert wp_routes.plugin_update_info("AI SEO Connector", installed)["update_available"] is expected


def test_old_vtechseo_agent_is_a_different_plugin_not_an_older_version():
    with _latest("1.5.0"):
        info = wp_routes.plugin_update_info("VtechSEO Agent", "1.0.0")
    assert info["update_available"] is True and info["reason"] == "different_plugin"


def test_unparseable_version_claims_nothing():
    with _latest("1.5.0"):
        assert wp_routes.plugin_update_info("AI SEO Connector", "dev-trunk")["update_available"] is None
        assert wp_routes.plugin_update_info("AI SEO Connector", None)["update_available"] is None


def test_github_unreachable_claims_nothing():
    with patch.object(wp_routes.httpx, "get", side_effect=httpx.ConnectError("down")):
        assert wp_routes.plugin_update_info("AI SEO Connector", "1.0.0") == {"checked": False}


def test_update_lookup_uses_a_short_timeout():
    """This rides on the connection test, which must stay a quick ping."""
    with patch.object(wp_routes, "_latest_plugin_release", return_value=None) as mock_latest:
        wp_routes.plugin_update_info("AI SEO Connector", "1.0.0")
    mock_latest.assert_called_once_with(timeout=3)
