"""
Schema drift detection. The incident behind it: migration 025 reported "Added
suggestion_revisions.verify_status" on two consecutive deploys, yet production
lacked the columns afterwards, and a page that read them returned 500.
In-memory SQLite; no network.
"""
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app import models, schema_check


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


def test_a_complete_schema_reports_no_drift(engine):
    assert schema_check.find_schema_drift(engine) == {}
    assert schema_check.schema_report(engine) == {"ok": True, "missing": {}}


def test_a_missing_column_is_reported_by_table_and_name(engine):
    """The exact production incident: the verify_* columns absent."""
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE suggestion_revisions DROP COLUMN verify_status"))
        conn.execute(text("ALTER TABLE suggestion_revisions DROP COLUMN verify_detail"))
    drift = schema_check.find_schema_drift(engine)
    assert drift == {"suggestion_revisions": ["verify_status", "verify_detail"]}
    report = schema_check.schema_report(engine)
    assert report["ok"] is False and report["missing"] == drift


def test_a_missing_table_is_reported(engine):
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE wordpress_connections"))
    assert schema_check.find_schema_drift(engine)["wordpress_connections"] == ["<table missing>"]


def test_detection_never_alters_the_database(engine):
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE suggestion_revisions DROP COLUMN verify_status"))
    schema_check.schema_report(engine)
    schema_check.log_drift_at_startup(engine)
    assert "verify_status" in schema_check.find_schema_drift(engine)["suggestion_revisions"]  # still missing: it only detects


def test_a_failure_to_read_the_schema_is_not_reported_as_ok():
    class Broken:
        def connect(self, *a, **k):
            raise RuntimeError("db unreachable")

    report = schema_check.schema_report(Broken())
    assert report["ok"] is None and "error" in report  # "could not tell", never a false all-clear


def test_startup_logs_an_error_when_drifted(engine, caplog):
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE suggestion_revisions DROP COLUMN verify_status"))
    with caplog.at_level(logging.ERROR, logger="schema_check"):
        schema_check.log_drift_at_startup(engine)
    assert any("SCHEMA DRIFT" in r.message and "verify_status" in r.message for r in caplog.records)


def test_startup_is_silent_when_the_schema_is_fine(engine, caplog):
    with caplog.at_level(logging.ERROR, logger="schema_check"):
        schema_check.log_drift_at_startup(engine)
    assert caplog.records == []


def test_version_endpoint_reports_the_schema_state(monkeypatch):
    """The deploy pipeline polls /version; a drifted DB must show up there."""
    from app import main

    monkeypatch.setattr(main.schema_check, "schema_report", lambda eng: {"ok": False, "missing": {"suggestion_revisions": ["verify_status"]}})
    body = TestClient(main.app).get("/version").json()
    assert body["schema"] == {"ok": False, "missing": {"suggestion_revisions": ["verify_status"]}}
    assert "commit" in body
