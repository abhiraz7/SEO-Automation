"""
Renders the project detail page (GET /projects/{id}) and checks the
Google Search Console card's three states: not-configured, not-connected,
and connected-with-properties (including the selected/not-selected
distinction). In-memory SQLite; no real HTTP to Google.
"""
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import google_search_console as gsc
from app import models
from app.database import get_db
from app.main import app

TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    seed = Session()
    try:
        yield TestClient(app), seed
    finally:
        seed.close()
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def make_project(db, name="P", base="https://client-site.com"):
    project = models.Project(name=name, base_url=base)
    db.add(project)
    db.commit()
    return project


def test_gsc_card_shows_not_configured_when_env_unset(env, monkeypatch):
    client, db = env
    for name in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REDIRECT_URI", "GSC_TOKEN_KEY"):
        monkeypatch.delenv(name, raising=False)
    project = make_project(db)
    resp = client.get(f"/projects/{project.id}")
    assert resp.status_code == 200
    assert "Not configured on this server" in resp.text
    assert "Connect Google Search Console" not in resp.text


def test_gsc_card_shows_connect_button_when_not_connected(env, monkeypatch):
    client, db = env
    for name, value in {
        "GOOGLE_OAUTH_CLIENT_ID": "x", "GOOGLE_OAUTH_CLIENT_SECRET": "y",
        "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:8000/gsc/callback", "GSC_TOKEN_KEY": TEST_KEY,
    }.items():
        monkeypatch.setenv(name, value)
    project = make_project(db)
    resp = client.get(f"/projects/{project.id}")
    assert resp.status_code == 200
    assert "Connect Google Search Console" in resp.text
    assert f'/projects/{project.id}/gsc/connect' in resp.text


def test_gsc_card_shows_connected_state_and_properties(env, monkeypatch):
    client, db = env
    for name, value in {
        "GOOGLE_OAUTH_CLIENT_ID": "x", "GOOGLE_OAUTH_CLIENT_SECRET": "y",
        "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:8000/gsc/callback", "GSC_TOKEN_KEY": TEST_KEY,
    }.items():
        monkeypatch.setenv(name, value)
    project = make_project(db)
    conn = models.GoogleConnection(
        project_id=project.id, google_account_email="agency@example.com",
        access_token_encrypted=gsc.encrypt_token("x"), refresh_token_encrypted=gsc.encrypt_token("y"),
        scope=" ".join(gsc.SCOPES), status="active",
    )
    db.add(conn)
    db.commit()
    db.add_all([
        models.SearchConsoleProperty(connection_id=conn.id, site_url="https://client-site.com/", permission_level="siteOwner", selected=True),
        models.SearchConsoleProperty(connection_id=conn.id, site_url="sc-domain:client-site.com", permission_level="siteFullUser", selected=False),
    ])
    db.commit()

    resp = client.get(f"/projects/{project.id}")
    assert resp.status_code == 200
    assert "Connected" in resp.text
    assert "agency@example.com" in resp.text
    assert "https://client-site.com/" in resp.text
    assert "sc-domain:client-site.com" in resp.text
    assert "Selected" in resp.text
    assert "Disconnect" in resp.text
    # The connect button must not still show once a connection exists
    assert "Connect Google Search Console" not in resp.text


def test_gsc_card_shows_reconnect_needed_when_revoked(env, monkeypatch):
    client, db = env
    for name, value in {
        "GOOGLE_OAUTH_CLIENT_ID": "x", "GOOGLE_OAUTH_CLIENT_SECRET": "y",
        "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:8000/gsc/callback", "GSC_TOKEN_KEY": TEST_KEY,
    }.items():
        monkeypatch.setenv(name, value)
    project = make_project(db)
    conn = models.GoogleConnection(
        project_id=project.id, google_account_email="agency@example.com",
        access_token_encrypted=gsc.encrypt_token("x"), refresh_token_encrypted=gsc.encrypt_token("y"),
        scope=" ".join(gsc.SCOPES), status="revoked",
    )
    db.add(conn)
    db.commit()

    resp = client.get(f"/projects/{project.id}")
    assert resp.status_code == 200
    assert "Reconnect needed" in resp.text
