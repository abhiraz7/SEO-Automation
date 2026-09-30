"""
GSC connect/callback/property-select route tests (Task 4). In-memory
SQLite; every google_search_console.py call is mocked -- no real OAuth,
no network. Covers: not-configured degrades cleanly, state mismatch/
expiry is rejected with 400, a successful callback stores the connection
and discovered properties, property selection enforces one-selected-per-
connection, and disconnect removes the connection.
"""
from unittest.mock import patch

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

_OAUTH_ENV = {
    "GOOGLE_OAUTH_CLIENT_ID": "client-id",
    "GOOGLE_OAUTH_CLIENT_SECRET": "client-secret",
    "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:8000/gsc/callback",
    "GSC_TOKEN_KEY": TEST_KEY,
}


@pytest.fixture
def env(monkeypatch):
    for name, value in _OAUTH_ENV.items():
        monkeypatch.setenv(name, value)

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
        yield TestClient(app, follow_redirects=False), seed
    finally:
        seed.close()
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def make_project(db, name="P", base="https://client-site.com"):
    project = models.Project(name=name, base_url=base)
    db.add(project)
    db.commit()
    return project


TOKEN_DATA = {
    "google_account_email": "agency@example.com",
    "access_token": "access-tok",
    "refresh_token": "refresh-tok",
    "expiry": None,
    "scope": " ".join(gsc.SCOPES),
}


def test_get_connection_not_configured(env, monkeypatch):
    client, db = env
    for name in _OAUTH_ENV:
        monkeypatch.delenv(name, raising=False)
    project = make_project(db)
    resp = client.get(f"/projects/{project.id}/gsc")
    assert resp.status_code == 200
    assert resp.json() == {"configured": False, "connected": False}


def test_get_connection_not_connected_yet(env):
    client, db = env
    project = make_project(db)
    resp = client.get(f"/projects/{project.id}/gsc")
    assert resp.status_code == 200
    assert resp.json() == {"configured": True, "connected": False}


def test_connect_unknown_project_404(env):
    client, db = env
    resp = client.get("/projects/999/gsc/connect")
    assert resp.status_code == 404


def test_connect_not_configured_501(env, monkeypatch):
    client, db = env
    for name in _OAUTH_ENV:
        monkeypatch.delenv(name, raising=False)
    project = make_project(db)
    resp = client.get(f"/projects/{project.id}/gsc/connect")
    assert resp.status_code == 501


def test_connect_redirects_to_google(env):
    client, db = env
    project = make_project(db)
    with patch.object(gsc, "build_auth_url", return_value="https://accounts.google.com/o/oauth2/auth?fake=1") as mock_build:
        resp = client.get(f"/projects/{project.id}/gsc/connect")
    assert resp.status_code in (302, 307)
    assert resp.headers["location"].startswith("https://accounts.google.com")
    mock_build.assert_called_once()


def test_callback_missing_code_or_state_400(env):
    client, db = env
    resp = client.get("/gsc/callback")
    assert resp.status_code == 400


def test_callback_google_error_400(env):
    client, db = env
    resp = client.get("/gsc/callback", params={"error": "access_denied"})
    assert resp.status_code == 400


def test_callback_invalid_state_400(env):
    client, db = env
    resp = client.get("/gsc/callback", params={"code": "abc", "state": "garbage"})
    assert resp.status_code == 400


def test_callback_state_for_unknown_project_404(env):
    client, db = env
    state = gsc.sign_state(999)
    resp = client.get("/gsc/callback", params={"code": "abc", "state": state})
    assert resp.status_code == 404


def test_callback_token_exchange_failure_502(env):
    client, db = env
    project = make_project(db)
    state = gsc.sign_state(project.id)
    with patch.object(gsc, "exchange_code_for_tokens", return_value=gsc.GSCResult(status="error", error="bad code")):
        resp = client.get("/gsc/callback", params={"code": "abc", "state": state})
    assert resp.status_code == 502


def test_callback_success_stores_connection_and_properties(env):
    client, db = env
    project = make_project(db)
    state = gsc.sign_state(project.id)
    sites = gsc.GSCResult(status="ok", data={"sites": [
        {"site_url": "https://client-site.com/", "permission_level": "siteFullUser"},
    ]})
    with patch.object(gsc, "exchange_code_for_tokens", return_value=gsc.GSCResult(status="ok", data=TOKEN_DATA)), \
         patch.object(gsc, "list_sites", return_value=sites):
        resp = client.get("/gsc/callback", params={"code": "abc", "state": state})
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == f"/projects/{project.id}#gsc"

    conn = db.query(models.GoogleConnection).filter(models.GoogleConnection.project_id == project.id).first()
    assert conn is not None
    assert conn.google_account_email == "agency@example.com"
    assert conn.status == "active"
    assert gsc.decrypt_token(conn.access_token_encrypted) == "access-tok"
    assert gsc.decrypt_token(conn.refresh_token_encrypted) == "refresh-tok"

    props = db.query(models.SearchConsoleProperty).filter(models.SearchConsoleProperty.connection_id == conn.id).all()
    assert len(props) == 1
    assert props[0].site_url == "https://client-site.com/"


def test_callback_success_with_no_properties_does_not_fail(env):
    """no_data from list_sites (a real, valid state -- zero accessible
    properties) must not turn a successful connect into an error."""
    client, db = env
    project = make_project(db)
    state = gsc.sign_state(project.id)
    with patch.object(gsc, "exchange_code_for_tokens", return_value=gsc.GSCResult(status="ok", data=TOKEN_DATA)), \
         patch.object(gsc, "list_sites", return_value=gsc.GSCResult(status="no_data", data={"sites": []})):
        resp = client.get("/gsc/callback", params={"code": "abc", "state": state})
    assert resp.status_code in (302, 307)
    conn = db.query(models.GoogleConnection).filter(models.GoogleConnection.project_id == project.id).first()
    assert conn is not None


def test_callback_reconnect_updates_existing_connection_not_duplicate(env):
    """Strictly one connection per project -- a second callback for the
    same project must update the existing row, never create a second one."""
    client, db = env
    project = make_project(db)

    for _ in range(2):
        state = gsc.sign_state(project.id)
        with patch.object(gsc, "exchange_code_for_tokens", return_value=gsc.GSCResult(status="ok", data=TOKEN_DATA)), \
             patch.object(gsc, "list_sites", return_value=gsc.GSCResult(status="no_data", data={"sites": []})):
            client.get("/gsc/callback", params={"code": "abc", "state": state})

    conns = db.query(models.GoogleConnection).filter(models.GoogleConnection.project_id == project.id).all()
    assert len(conns) == 1


def test_select_property_enforces_single_selection(env):
    client, db = env
    project = make_project(db)
    conn = models.GoogleConnection(
        project_id=project.id, google_account_email="a@example.com",
        access_token_encrypted=gsc.encrypt_token("x"), refresh_token_encrypted=gsc.encrypt_token("y"),
        scope=" ".join(gsc.SCOPES), status="active",
    )
    db.add(conn)
    db.commit()
    p1 = models.SearchConsoleProperty(connection_id=conn.id, site_url="https://a.com/", selected=True)
    p2 = models.SearchConsoleProperty(connection_id=conn.id, site_url="https://b.com/", selected=False)
    db.add_all([p1, p2])
    db.commit()

    resp = client.post(f"/projects/{project.id}/gsc/properties/{p2.id}/select")
    assert resp.status_code == 200

    db.refresh(p1)
    db.refresh(p2)
    assert p1.selected is False
    assert p2.selected is True


def test_select_property_wrong_connection_404(env):
    client, db = env
    project_a = make_project(db, name="A", base="https://a.com")
    project_b = make_project(db, name="B", base="https://b.com")
    conn_a = models.GoogleConnection(
        project_id=project_a.id, google_account_email="a@example.com",
        access_token_encrypted=gsc.encrypt_token("x"), refresh_token_encrypted=gsc.encrypt_token("y"),
        scope=" ".join(gsc.SCOPES), status="active",
    )
    conn_b = models.GoogleConnection(
        project_id=project_b.id, google_account_email="b@example.com",
        access_token_encrypted=gsc.encrypt_token("x"), refresh_token_encrypted=gsc.encrypt_token("y"),
        scope=" ".join(gsc.SCOPES), status="active",
    )
    db.add_all([conn_a, conn_b])
    db.commit()
    prop_b = models.SearchConsoleProperty(connection_id=conn_b.id, site_url="https://b.com/")
    db.add(prop_b)
    db.commit()

    # Trying to select project B's property through project A's route
    resp = client.post(f"/projects/{project_a.id}/gsc/properties/{prop_b.id}/select")
    assert resp.status_code == 404


def test_disconnect_removes_connection_and_properties(env):
    client, db = env
    project = make_project(db)
    conn = models.GoogleConnection(
        project_id=project.id, google_account_email="a@example.com",
        access_token_encrypted=gsc.encrypt_token("x"), refresh_token_encrypted=gsc.encrypt_token("y"),
        scope=" ".join(gsc.SCOPES), status="active",
    )
    db.add(conn)
    db.commit()
    db.add(models.SearchConsoleProperty(connection_id=conn.id, site_url="https://a.com/"))
    db.commit()

    conn_id = conn.id
    resp = client.post(f"/projects/{project.id}/gsc/disconnect")
    assert resp.status_code == 200
    assert resp.json() == {"disconnected": True}

    # The route's own DB session (a separate one from this test's `db`,
    # per the override) committed the delete -- expire this session's
    # identity map so it re-reads from the DB instead of erroring on a
    # locally-cached, now-stale `conn` object.
    db.expire_all()
    assert db.query(models.GoogleConnection).filter(models.GoogleConnection.project_id == project.id).first() is None
    assert db.query(models.SearchConsoleProperty).filter(models.SearchConsoleProperty.connection_id == conn_id).count() == 0


def test_disconnect_when_never_connected_is_a_no_op(env):
    client, db = env
    project = make_project(db)
    resp = client.post(f"/projects/{project.id}/gsc/disconnect")
    assert resp.status_code == 200
    assert resp.json() == {"disconnected": True}
