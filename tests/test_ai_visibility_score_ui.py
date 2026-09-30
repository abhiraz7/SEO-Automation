"""
Renders /visibility and /projects/{id} and checks the AI Visibility Score
shows up correctly in each of its states: no data yet, and a real
percentage. In-memory SQLite, no real DataForSEO calls.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
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


def test_visibility_report_shows_no_data_state(env):
    client, db = env
    project = make_project(db)
    resp = client.get(f"/projects/{project.id}/visibility")
    assert resp.status_code == 200
    assert "No AI Overview has appeared yet" in resp.text


def test_visibility_report_shows_score_percentage(env):
    client, db = env
    project = make_project(db)
    db.add_all([
        models.VisibilityCheck(project_id=project.id, query="q1", ai_overview_present=True, brand_in_ai_overview=True, organic_rank=3),
        models.VisibilityCheck(project_id=project.id, query="q2", ai_overview_present=True, brand_in_ai_overview=False, organic_rank=9),
    ])
    db.commit()
    resp = client.get(f"/projects/{project.id}/visibility")
    assert resp.status_code == 200
    assert "50%" in resp.text
    assert "Cited in 1 of 2 checks" in resp.text
    assert "avg. organic rank 6.0" in resp.text


def test_project_detail_shows_ai_visibility_pill(env):
    client, db = env
    project = make_project(db)
    db.add_all([
        models.VisibilityCheck(project_id=project.id, query="q1", ai_overview_present=True, brand_in_ai_overview=True),
    ])
    db.commit()
    resp = client.get(f"/projects/{project.id}")
    assert resp.status_code == 200
    assert "AI Visibility: 100%" in resp.text


def test_project_detail_shows_no_data_pill_when_no_checks(env):
    client, db = env
    project = make_project(db)
    resp = client.get(f"/projects/{project.id}")
    assert resp.status_code == 200
    assert "AI Visibility: No data yet" in resp.text
