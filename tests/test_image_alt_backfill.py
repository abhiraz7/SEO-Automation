"""
Regression: the per-image alt-text fix modal (baea87c/3bacd8f, 2026-09-15/20)
only ever fills in Page.image_alts at crawl-ingestion time. Production's last
real crawl of every image_alt-flagged page predated that feature, so every
one of those pages was stuck with image_alts=[] forever -- the fix modal
showed "Could not read this page's images directly (fetch failed)" even
though no fetch had ever been attempted, with no way to re-fetch short of a
billed, capped re-crawl. Confirmed live against production on 2026-10-01: all
17 image_alt issues on project 1 had an empty missing_alt_images list.

Fixed by: dataforseo_onpage.fetch_image_alts() now returns None (not []) on
a failed fetch, so "never/failed to fetch" and "fetched, found nothing" are
no longer the same value; and a new on-demand
POST /projects/{id}/onpage/pages/{id}/image-alts/refresh route the fix modal
calls automatically for exactly this case (see onpage_semrush.html's
fmMaybeBackfillImages).
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import audit_classification, models
from app.database import get_db
from app.main import app


@pytest.fixture
def client_and_ids():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    session = Session()
    project = models.Project(name="t", base_url="https://example.com")
    session.add(project)
    session.flush()
    # The exact production state: a page DataForSEO flagged no_image_alt on,
    # but whose image_alts was never actually filled in (pre-feature crawl).
    page = models.Page(
        project_id=project.id, url="https://example.com/post", source="dataforseo",
        checks={"no_image_alt": True}, image_alts=[],
    )
    session.add(page)
    session.flush()
    issue = models.Issue(
        project_id=project.id, page_id=page.id, category="image_alt", rule="missing",
        severity="warning", message="One or more images are missing alt text.",
        **audit_classification.classify("image_alt", "missing"),
    )
    session.add(issue)
    session.commit()
    ids = {"project_id": project.id, "page_id": page.id, "issue_id": issue.id}
    session.close()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app), ids, Session
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def _refresh_url(ids):
    return f"/projects/{ids['project_id']}/onpage/pages/{ids['page_id']}/image-alts/refresh"


def test_stale_page_shows_no_images_before_backfill(client_and_ids):
    """Reproduces the production symptom directly: the on-page view's
    missing_alt_images for this issue is empty, exactly like every real
    image_alt issue in production before this fix."""
    client, ids, _ = client_and_ids
    resp = client.get(f"/projects/{ids['project_id']}/onpage")
    assert resp.status_code == 200
    import re
    m = re.search(r"var ISSUES_JS = (\{.*?\});\n", resp.text, re.S)
    import json
    issues = json.loads(m.group(1))
    issue_js = issues[str(ids["issue_id"])]
    assert issue_js["category"] == "image_alt"
    assert issue_js["missing_alt_images"] == []


def test_refresh_backfills_images_for_a_stale_page(client_and_ids):
    """The fix: calling the new refresh route for that same stale page
    fetches the real page HTML, stores the images, and returns them in the
    shape the fix modal expects (src/alt/also_on/media_id)."""
    client, ids, Session = client_and_ids
    fetched = [
        {"src": "https://example.com/logo.png", "alt": "", "media_id": None},
        {"src": "https://example.com/hero.jpg", "alt": "A hero banner", "media_id": 42},
    ]
    with patch("app.routes.onpage_semrush.dataforseo_onpage.fetch_image_alts", return_value=fetched) as m:
        resp = client.post(_refresh_url(ids))
    assert resp.status_code == 200
    m.assert_called_once_with("https://example.com/post")
    body = resp.json()
    # hero.jpg has alt text already -- only the genuinely missing one comes back.
    assert [img["src"] for img in body["images"]] == ["https://example.com/logo.png"]
    assert body["images"][0]["also_on"] == []

    # And the stale page is actually fixed in storage, not just in this response.
    s = Session()
    page = s.get(models.Page, ids["page_id"])
    assert page.image_alts == fetched
    s.close()


def test_refresh_failure_is_reported_and_does_not_erase_existing_data(client_and_ids):
    """A failed fetch (site blocking the request, timeout, ...) must say so,
    not silently report 'no images' -- and must never wipe out image data a
    previous successful fetch already stored."""
    client, ids, Session = client_and_ids
    s = Session()
    page = s.get(models.Page, ids["page_id"])
    page.image_alts = [{"src": "https://example.com/old.png", "alt": None, "media_id": None}]
    s.commit()
    s.close()

    with patch("app.routes.onpage_semrush.dataforseo_onpage.fetch_image_alts", return_value=None):
        resp = client.post(_refresh_url(ids))
    assert resp.status_code == 502
    assert "Could not fetch" in resp.json()["detail"]

    s = Session()
    page = s.get(models.Page, ids["page_id"])
    assert page.image_alts == [{"src": "https://example.com/old.png", "alt": None, "media_id": None}]
    s.close()


def test_refresh_rejects_a_page_from_another_project(client_and_ids):
    client, ids, Session = client_and_ids
    s = Session()
    other = models.Project(name="other", base_url="https://other.example.com")
    s.add(other)
    s.commit()
    other_id = other.id
    s.close()
    resp = client.post(f"/projects/{other_id}/onpage/pages/{ids['page_id']}/image-alts/refresh")
    assert resp.status_code == 404


def test_also_on_reports_the_other_page_sharing_the_same_image(client_and_ids):
    """A template image (logo, footer banner) missing alt text on several
    pages should say so, computed from the OTHER already-fetched pages in
    the same project -- not just the one page.image_alts just rewrote."""
    client, ids, Session = client_and_ids
    s = Session()
    sibling = models.Page(
        project_id=ids["project_id"], url="https://example.com/other-post", source="dataforseo",
        image_alts=[{"src": "https://example.com/logo.png", "alt": "", "media_id": None}],
    )
    s.add(sibling)
    s.commit()
    s.close()

    fetched = [{"src": "https://example.com/logo.png", "alt": "", "media_id": None}]
    with patch("app.routes.onpage_semrush.dataforseo_onpage.fetch_image_alts", return_value=fetched):
        resp = client.post(_refresh_url(ids))
    assert resp.status_code == 200
    assert resp.json()["images"][0]["also_on"] == ["https://example.com/other-post"]


def test_fetch_image_alts_sends_the_crawler_user_agent_and_logs_on_failure(caplog):
    """A bare python-httpx request gets blocked outright by some sites'
    bot protection (seen live, Cloudflare) -- the request must identify
    itself the same way app/crawler.py does. And a failure must be logged
    (project rule: every failure is logged, not just swallowed), with the
    happy path logging nothing."""
    import logging
    from app import dataforseo_onpage
    from app.html_extract import USER_AGENT

    class _Boom:
        def raise_for_status(self):
            raise RuntimeError("blocked")

    with patch("app.dataforseo_onpage.httpx.get", return_value=_Boom()) as m, caplog.at_level(logging.WARNING):
        result = dataforseo_onpage.fetch_image_alts("https://example.com/")

    assert result is None  # not [] -- see this module's docstring
    assert m.call_args.kwargs["headers"] == {"User-Agent": USER_AGENT}
    records = [r for r in caplog.records if r.name == "dataforseo_onpage"]
    assert len(records) == 1
    assert "image_alts_fetch_failed" in records[0].message
    assert "example.com" in records[0].message


def test_fetch_image_alts_logs_nothing_on_success():
    import logging
    from unittest.mock import MagicMock
    from app import dataforseo_onpage

    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.text = "<html><body><img src='/a.png'></body></html>"
    logger = logging.getLogger("dataforseo_onpage")
    with patch("app.dataforseo_onpage.httpx.get", return_value=resp):
        with patch.object(logger, "warning") as warn, patch.object(logger, "exception") as exc:
            result = dataforseo_onpage.fetch_image_alts("https://example.com/")
    assert result == [{"src": "https://example.com/a.png", "alt": None, "media_id": None}]
    warn.assert_not_called()
    exc.assert_not_called()
