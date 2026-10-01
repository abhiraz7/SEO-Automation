"""Settings: API key STATUS (never values) and the raw API error log."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.database import get_db
from app.main import app

SECRET = "sk-ant-api03-SUPERSECRETVALUE-1234567890abcdef"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv("SEMRUSH_API_KEY", raising=False)     # keep /settings from making a live SEMrush health call
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app), Session
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def add_log(Session, level, logger, message, n=1):
    with Session() as s:
        for i in range(n):
            s.add(models.AppLog(level=level, logger=logger, message=message.format(i=i),
                                created_at=datetime(2026, 9, 30, 12, 0, i % 60, tzinfo=timezone.utc)))
        s.commit()


def test_page_shows_key_status_but_never_a_value(env):
    client, _ = env
    html = client.get("/settings").text
    assert "API keys" in html and "ANTHROPIC_API_KEY" in html
    assert "SUPERSECRET" not in html and SECRET not in html and "sk-ant-api03" not in html
    assert "ends …cdef" in html            # tail only
    assert "Missing" in html               # the keys that are not set


def test_empty_log_renders_a_helpful_message(env):
    client, _ = env
    html = client.get("/settings").text
    assert "API error log" in html and "No log entries" in html
    assert "Clear the log" not in html      # nothing to clear


def test_entries_are_shown_raw_newest_first_with_tracebacks(env):
    client, Session = env
    add_log(Session, "WARNING", "dataforseo", "dataforseo.api_error path=/serp code=40210 reason=\"Insufficient funds\"")
    add_log(Session, "ERROR", "suggestions", "suggestions.provider_failed project=3\nTraceback (most recent call last):\n  File \"x.py\", line 1\nKeyError: 'ANTHROPIC_API_KEY'")
    html = client.get("/settings").text
    assert html.index("suggestions.provider_failed") < html.index("dataforseo.api_error")      # newest first
    assert "Traceback (most recent call last)" in html and "KeyError" in html
    assert "Showing 2 of 2 matching entries" in html


def test_log_text_is_html_escaped(env):
    client, Session = env
    add_log(Session, "ERROR", "uvicorn.error", "bad page <script>alert('x')</script> <img src=x onerror=alert(1)>")
    html = client.get("/settings").text
    assert "<script>alert('x')</script>" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html


def test_filters_by_level_and_text(env):
    client, Session = env
    add_log(Session, "WARNING", "dataforseo", "dataforseo.api_error code=40210")
    add_log(Session, "ERROR", "semrush", "semrush.request_failed 403")
    only_err = client.get("/settings", params={"level": "ERROR"}).text
    assert "semrush.request_failed" in only_err and "dataforseo.api_error" not in only_err
    by_text = client.get("/settings", params={"q": "40210"}).text
    assert "dataforseo.api_error" in by_text and "semrush.request_failed" not in by_text
    assert "Showing 0 of 0" in client.get("/settings", params={"q": "nothing-matches-this"}).text


def test_download_is_plain_text_and_respects_filters(env):
    client, Session = env
    add_log(Session, "WARNING", "dataforseo", "dataforseo.api_error code=40210")
    add_log(Session, "ERROR", "semrush", "semrush.request_failed 403")
    r = client.get("/settings/logs.txt", params={"level": "ERROR"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert "semrush.request_failed 403" in r.text and "dataforseo" not in r.text
    assert "ERROR  semrush" in r.text
    assert client.get("/settings/logs.txt", params={"q": "zzz"}).text == "(no log entries)"


def test_limit_is_clamped(env):
    client, Session = env
    add_log(Session, "WARNING", "x", "entry {i}", n=30)
    assert "Showing 5 of 30" in client.get("/settings", params={"limit": 5}).text
    assert "Showing 30 of 30" in client.get("/settings", params={"limit": 999999}).text
    assert "Showing 1 of 30" in client.get("/settings", params={"limit": 0}).text


def test_clear_deletes_every_entry_and_redirects_back(env):
    client, Session = env
    add_log(Session, "ERROR", "x", "gone soon", n=3)
    r = client.post("/settings/logs/clear", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings#logs"
    with Session() as s:
        assert s.query(models.AppLog).count() == 0
