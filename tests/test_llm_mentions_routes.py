"""
POST /projects/{id}/llm-mentions/refresh route tests. dataforseo.py is
mocked -- no real HTTP, no real spend. In-memory SQLite.
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import dataforseo, models
from app.database import get_db
from app.main import app


@pytest.fixture
def env():
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


def test_refresh_stores_a_successful_snapshot(env):
    client, db = env
    project = make_project(db)
    ok_result = {"total_mentions": 42, "ai_search_volume": 1000, "sources_domain": [{"key": "reddit.com", "mentions": 10}], "cost": 0.101, "error": None}
    with patch.object(dataforseo, "fetch_llm_mentions_target_metrics", return_value=ok_result):
        resp = client.post(f"/projects/{project.id}/llm-mentions/refresh")
    assert resp.status_code in (302, 303)

    snapshot = db.query(models.LlmMentionSnapshot).filter(models.LlmMentionSnapshot.project_id == project.id).first()
    assert snapshot is not None
    assert snapshot.total_mentions == 42
    assert snapshot.error is None


def test_refresh_stores_error_snapshot_without_crashing(env):
    client, db = env
    project = make_project(db)
    error_result = {"total_mentions": None, "ai_search_volume": None, "sources_domain": None, "cost": None, "error": "DataForSEO not configured"}
    with patch.object(dataforseo, "fetch_llm_mentions_target_metrics", return_value=error_result):
        resp = client.post(f"/projects/{project.id}/llm-mentions/refresh")
    assert resp.status_code in (302, 303)

    snapshot = db.query(models.LlmMentionSnapshot).filter(models.LlmMentionSnapshot.project_id == project.id).first()
    assert snapshot is not None
    assert snapshot.error == "DataForSEO not configured"
    assert snapshot.total_mentions is None


def test_refresh_unknown_project_does_not_crash(env):
    client, db = env
    resp = client.post("/projects/999/llm-mentions/refresh")
    assert resp.status_code in (302, 303)
    assert db.query(models.LlmMentionSnapshot).count() == 0


def test_visibility_report_shows_not_checked_yet(env):
    client, db = env
    project = make_project(db)
    resp = client.get(f"/projects/{project.id}/visibility")
    assert resp.status_code == 200
    assert "Not checked yet" in resp.text


def test_visibility_report_shows_latest_snapshot(env):
    client, db = env
    project = make_project(db)
    db.add(models.LlmMentionSnapshot(
        project_id=project.id, total_mentions=42, ai_search_volume=5000,
        sources_domain=[{"key": "reddit.com", "mentions": 10, "ai_search_volume": 500}],
        cost=0.101,
    ))
    db.commit()
    resp = client.get(f"/projects/{project.id}/visibility")
    assert resp.status_code == 200
    assert "42" in resp.text
    assert "5,000" in resp.text
    assert "reddit.com" in resp.text


def test_visibility_report_shows_snapshot_error(env):
    client, db = env
    project = make_project(db)
    db.add(models.LlmMentionSnapshot(project_id=project.id, error="DataForSEO not configured"))
    db.commit()
    resp = client.get(f"/projects/{project.id}/visibility")
    assert resp.status_code == 200
    assert "DataForSEO not configured" in resp.text
