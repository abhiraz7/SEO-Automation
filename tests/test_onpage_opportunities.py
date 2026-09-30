"""
On-page screen: findings that don't count toward Site Health (missing
canonical/OG/Twitter/meta description, thin-content ratio) must stay VISIBLE
but be labelled "Opportunity", and the Critical/Warnings counts must cover
scored issues only. See app/audit_classification.py. In-memory SQLite.
"""
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import audit_classification, models
from app.database import get_db
from app.main import app


@pytest.fixture
def client_and_project():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    session = Session()
    project = models.Project(name="t", base_url="https://example.com")
    session.add(project)
    session.flush()
    page = models.Page(project_id=project.id, url="https://example.com/", source="dataforseo")
    session.add(page)
    session.flush()
    rules = [
        ("title", "missing", "error"),          # scored error
        ("title", "too_long", "warning"),       # scored warning
        ("canonical", "missing", "warning"),    # opportunity
        ("opengraph", "missing", "warning"),    # opportunity
        ("twitter", "missing", "warning"),      # opportunity
    ]
    for category, rule, severity in rules:
        session.add(models.Issue(
            project_id=project.id, page_id=page.id, category=category, rule=rule,
            severity=severity, message=f"{category} {rule}",
            **audit_classification.classify(category, rule),
        ))
    session.commit()
    project_id = project.id
    session.close()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app), project_id
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def _page(client_and_project):
    client, project_id = client_and_project
    resp = client.get(f"/projects/{project_id}/onpage")
    assert resp.status_code == 200
    return resp.text


def test_unscored_findings_stay_visible_as_opportunities(client_and_project):
    html = _page(client_and_project)
    assert len(re.findall(r'data-severity="opportunity"', html)) == 3
    # the findings are still in the list, not dropped
    assert "canonical missing" in html and "twitter missing" in html


def test_scored_findings_keep_their_severity(client_and_project):
    html = _page(client_and_project)
    assert len(re.findall(r'data-severity="error"', html)) == 1
    assert len(re.findall(r'data-severity="warning"', html)) == 1


def test_kpi_counts_are_scored_only(client_and_project):
    html = _page(client_and_project)
    # Opportunities card shows 3; Critical Errors 1; Warnings 1
    assert re.search(r'Opportunities</div>\s*<div class="kpi-number"[^>]*>3<', html)
    assert re.search(r'Critical Errors</div>\s*<div class="kpi-number"[^>]*>1<', html)
    assert re.search(r'Warnings</div>\s*<div class="kpi-number"[^>]*>1<', html)


def test_content_category_is_relabelled():
    from app.routes import onpage_semrush
    assert "Content Signals" in onpage_semrush.CATEGORY_LABELS["content"]
    assert "Quality" not in onpage_semrush.CATEGORY_LABELS["content"]
