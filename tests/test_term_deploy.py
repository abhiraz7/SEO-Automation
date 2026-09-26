"""
Taxonomy-term deploys, the URL resolvers behind them, and the "this page can
never be deployed" reasons. No network: httpx.get and the plugin calls are
mocked; the database is an in-memory SQLite.

The failure these guard against: a term page such as /subject/hindi/ used to
fall through to a manual "enter a post id" prompt, and any number typed would
have been written to an unrelated POST.
"""
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models, wordpress
from app.routes import wordpress as wp_routes

SITE = "https://site.com"


# ── helpers ──────────────────────────────────────────────────────────────

def _resp(data, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = data
    return r


def _routes(mapping):
    """httpx.get replacement: match on the URL suffix, ignore params."""
    def fake_get(url, params=None, **kwargs):
        for suffix, data in mapping.items():
            if url.endswith(suffix):
                if isinstance(data, Exception):
                    raise data
                return _resp(data)
        return _resp([], 404)
    return fake_get


# ── resolve_post_id_by_url: duplicate slugs ──────────────────────────────

def _post_get(pages_payload):
    return _routes({"/wp-json/wp/v2/types": {}, "/wp-json/wp/v2/posts": [], "/wp-json/wp/v2/pages": pages_payload})


def test_duplicate_slug_is_resolved_by_link():
    payload = [
        {"id": 8419, "link": "https://site.com/buy-backlinks/premium-plan/"},
        {"id": 7112, "link": "https://site.com/google-stacking/premium-plan/"},
    ]
    with patch.object(wordpress.httpx, "get", side_effect=_post_get(payload)):
        a = wordpress.resolve_post_id_by_url(SITE, "https://site.com/buy-backlinks/premium-plan/")
        b = wordpress.resolve_post_id_by_url(SITE, "https://site.com/google-stacking/premium-plan/")
    assert (a.status, a.data["post_id"]) == ("ok", 8419)
    assert (b.status, b.data["post_id"]) == ("ok", 7112)


def test_duplicate_slug_with_double_slash_url_still_matches():
    payload = [
        {"id": 8419, "link": "https://site.com/buy-backlinks/premium-plan/"},
        {"id": 7112, "link": "https://site.com/google-stacking/premium-plan/"},
    ]
    with patch.object(wordpress.httpx, "get", side_effect=_post_get(payload)):
        r = wordpress.resolve_post_id_by_url(SITE, "https://site.com//google-stacking/premium-plan/")
    assert (r.status, r.data["post_id"]) == ("ok", 7112)


def test_duplicate_slug_with_no_exact_link_is_still_ambiguous():
    payload = [
        {"id": 1, "link": "https://site.com/a/premium-plan/"},
        {"id": 2, "link": "https://site.com/b/premium-plan/"},
    ]
    with patch.object(wordpress.httpx, "get", side_effect=_post_get(payload)):
        r = wordpress.resolve_post_id_by_url(SITE, "https://site.com/c/premium-plan/")
    assert r.status == "no_data"  # never guess between two candidates


# ── resolve_term_by_url ──────────────────────────────────────────────────

TAXONOMIES = {
    "category": {"rest_base": "categories"},
    "subject": {"rest_base": "subject"},
    "nav_menu": {"rest_base": "menus"},
}


def test_term_found_by_slug_and_link():
    get = _routes({
        "/wp-json/wp/v2/taxonomies": TAXONOMIES,
        "/wp-json/wp/v2/categories": [],
        "/wp-json/wp/v2/subject": [{"id": 52, "link": "https://site.com/subject/hindi/", "taxonomy": "subject"}],
    })
    with patch.object(wordpress.httpx, "get", side_effect=get):
        r = wordpress.resolve_term_by_url(SITE, "https://site.com/subject/hindi/")
    assert r.status == "ok" and r.data == {"taxonomy": "subject", "term_id": 52}


def test_term_with_same_slug_but_different_link_is_not_picked():
    get = _routes({
        "/wp-json/wp/v2/taxonomies": TAXONOMIES,
        "/wp-json/wp/v2/categories": [{"id": 9, "link": "https://site.com/category/hindi/", "taxonomy": "category"}],
        "/wp-json/wp/v2/subject": [],
    })
    with patch.object(wordpress.httpx, "get", side_effect=get):
        r = wordpress.resolve_term_by_url(SITE, "https://site.com/subject/hindi/")
    assert r.status == "no_data"


def test_two_terms_matching_the_same_link_is_refused_not_guessed():
    same = "https://site.com/x/hindi/"
    get = _routes({
        "/wp-json/wp/v2/taxonomies": TAXONOMIES,
        "/wp-json/wp/v2/categories": [{"id": 1, "link": same, "taxonomy": "category"}],
        "/wp-json/wp/v2/subject": [{"id": 2, "link": same, "taxonomy": "subject"}],
    })
    with patch.object(wordpress.httpx, "get", side_effect=get):
        r = wordpress.resolve_term_by_url(SITE, same)
    assert r.status == "no_data"


def test_internal_taxonomies_are_never_queried():
    seen = []

    def get(url, params=None, **kwargs):
        seen.append(url)
        if url.endswith("/taxonomies"):
            return _resp(TAXONOMIES)
        return _resp([])

    with patch.object(wordpress.httpx, "get", side_effect=get):
        wordpress.resolve_term_by_url(SITE, "https://site.com/subject/hindi/")
    assert not any(u.endswith("/menus") for u in seen)


def test_term_lookup_never_raises_when_site_is_down():
    with patch.object(wordpress.httpx, "get", side_effect=httpx.ConnectError("down")):
        r = wordpress.resolve_term_by_url(SITE, "https://site.com/subject/hindi/")
    assert r.status == "no_data"


def test_homepage_url_has_no_term_slug():
    r = wordpress.resolve_term_by_url(SITE, "https://site.com/")
    assert r.status == "no_data"


# ── non_deployable_reason ────────────────────────────────────────────────

@pytest.mark.parametrize("url,reason", [
    ("https://site.com/free-notes/?exam=ctet", "query_url"),
    ("https://site.com/blog/page/2/", "pagination"),
    ("https://site.com/wp-content/uploads/2024/08/pic.png", "media_file"),
    ("https://site.com/cdn-cgi/l/email-protection", "infrastructure"),
    ("https://site.com/blog/feed/", "feed"),
])
def test_non_deployable_reasons(url, reason):
    assert wordpress.non_deployable_reason(url) == reason
    assert wordpress.non_deployable_message(reason)


@pytest.mark.parametrize("url", [
    "https://site.com/", "https://site.com/subject/hindi/", "https://site.com/about/",
    "https://site.com/page/", "https://site.com/my-page-2/", "https://site.com/page-builder/x/",
])
def test_normal_urls_are_not_flagged(url):
    assert wordpress.non_deployable_reason(url) is None


# ── deploy / rollback routing ────────────────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed(db, url, category="meta_description", page_wp_post_id=None):
    project = models.Project(name="P", base_url=SITE)
    db.add(project)
    db.flush()
    page = models.Page(project_id=project.id, url=url, wp_post_id=page_wp_post_id)
    db.add(page)
    db.flush()
    issue = models.Issue(project_id=project.id, page_id=page.id, category=category, rule="missing", message="m")
    db.add(issue)
    db.flush()
    sug = models.Suggestion(project_id=project.id, page_id=page.id, issue_id=issue.id, content="New text", status="accepted")
    db.add(sug)
    db.commit()
    conn = models.WordPressConnection(project_id=project.id, site_url=SITE, api_token="x", last_verify_ok=True)
    return project, page, sug, conn


def _ok(data):
    return wordpress.WordPressResult(status="ok", data=data)


def _no_data():
    return wordpress.WordPressResult(status="no_data", data={})


@pytest.fixture
def plugin():
    """Patches everything that would touch the network."""
    with patch.object(wp_routes, "_connected_or_error") as conn, \
         patch.object(wordpress, "resolve_post_id_by_url") as post, \
         patch.object(wordpress, "resolve_term_by_url") as term, \
         patch.object(wordpress, "get_term_seo") as get_term, \
         patch.object(wordpress, "set_term_seo") as set_term, \
         patch.object(wordpress, "get_yoast_meta") as get_post_seo, \
         patch.object(wordpress, "set_yoast_meta") as set_post_seo:
        get_term.return_value = _ok({"meta_description": "old term desc", "seo_title": "old term title"})
        set_term.return_value = _ok({"taxonomy": "subject", "term_id": 52, "updated_fields": ["meta_description"]})
        get_post_seo.return_value = _ok({"meta_description": "old post desc"})
        set_post_seo.return_value = _ok({})
        yield MagicMock(conn=conn, post=post, term=term, get_term=get_term, set_term=set_term,
                        get_post_seo=get_post_seo, set_post_seo=set_post_seo)


def _deploy(db, sug, conn_row, plugin, wp_post_id=None):
    plugin.conn.return_value = (conn_row, "tok")
    return wp_routes.deploy_suggestion(sug.id, wp_routes.DeployIn(wp_post_id=wp_post_id), db)


def test_term_page_deploys_to_the_term_not_a_post(db, plugin):
    _, page, sug, conn = _seed(db, "https://site.com/subject/hindi/")
    plugin.post.return_value = _no_data()
    plugin.term.return_value = _ok({"taxonomy": "subject", "term_id": 52})

    rev = _deploy(db, sug, conn, plugin)

    plugin.set_term.assert_called_once_with(SITE, "tok", "subject", 52, meta_description="New text")
    plugin.set_post_seo.assert_not_called()  # the wrong-object write this exists to prevent
    assert rev["wp_post_id"] == 52 and rev["deployed_via"] == "seo_set_term_meta"
    assert rev["before_value"] == "old term desc"
    db.refresh(page)
    assert page.wp_post_id is None  # a TERM id must never be cached where a post id lives
    db.refresh(sug)
    assert sug.status == "deployed"


def test_term_page_title_uses_seo_title_field(db, plugin):
    _, _, sug, conn = _seed(db, "https://site.com/subject/hindi/", category="title")
    plugin.post.return_value = _no_data()
    plugin.term.return_value = _ok({"taxonomy": "subject", "term_id": 52})
    _deploy(db, sug, conn, plugin)
    plugin.set_term.assert_called_once_with(SITE, "tok", "subject", 52, seo_title="New text")


def test_unsupported_field_on_a_term_is_refused_and_writes_nothing(db, plugin):
    _, _, sug, conn = _seed(db, "https://site.com/subject/hindi/", category="h1")
    plugin.post.return_value = _no_data()
    plugin.term.return_value = _ok({"taxonomy": "subject", "term_id": 52})

    with pytest.raises(HTTPException) as e:
        _deploy(db, sug, conn, plugin)

    assert e.value.status_code == 422 and e.value.detail["reason"] == "term_field_unsupported"
    plugin.set_term.assert_not_called()
    plugin.set_post_seo.assert_not_called()
    assert db.query(models.SuggestionRevision).count() == 0


def test_query_url_is_refused_with_a_reason_before_any_lookup(db, plugin):
    _, _, sug, conn = _seed(db, "https://site.com/free-notes/?exam=ctet")
    with pytest.raises(HTTPException) as e:
        _deploy(db, sug, conn, plugin)
    assert e.value.status_code == 422 and e.value.detail["reason"] == "query_url"
    plugin.post.assert_not_called()  # no lookup that could mis-match a look-alike slug
    plugin.term.assert_not_called()


def test_unresolvable_page_still_asks_for_a_post_id(db, plugin):
    _, _, sug, conn = _seed(db, "https://site.com/mystery/")
    plugin.post.return_value = _no_data()
    plugin.term.return_value = _no_data()
    with pytest.raises(HTTPException) as e:
        _deploy(db, sug, conn, plugin)
    assert e.value.status_code == 400  # the existing manual-entry prompt is unchanged


def test_post_page_still_uses_the_post_tools(db, plugin):
    """Regression: the term work must not change how ordinary posts deploy."""
    _, page, sug, conn = _seed(db, "https://site.com/some-post/", page_wp_post_id=77)
    rev = _deploy(db, sug, conn, plugin)
    plugin.set_post_seo.assert_called_once_with(SITE, "tok", 77, meta_description="New text")
    plugin.set_term.assert_not_called()
    assert rev["wp_post_id"] == 77 and rev["deployed_via"] == "yoast_set_meta"


def test_explicit_manual_id_is_always_a_post_id(db, plugin):
    _, page, sug, conn = _seed(db, "https://site.com/mystery/")
    rev = _deploy(db, sug, conn, plugin, wp_post_id=123)
    plugin.set_post_seo.assert_called_once_with(SITE, "tok", 123, meta_description="New text")
    plugin.set_term.assert_not_called()
    db.refresh(page)
    assert page.wp_post_id == 123 and rev["deployed_via"] == "yoast_set_meta"


def test_term_deploy_can_be_rolled_back_to_the_recorded_taxonomy(db, plugin):
    _, _, sug, conn = _seed(db, "https://site.com/subject/hindi/")
    plugin.post.return_value = _no_data()
    plugin.term.return_value = _ok({"taxonomy": "subject", "term_id": 52})
    rev = _deploy(db, sug, conn, plugin)
    plugin.set_term.reset_mock()
    plugin.get_term.return_value = _ok({})

    # Empty before-value is restored as "" (the plugin deletes the custom value).
    row = db.get(models.SuggestionRevision, rev["id"])
    row.before_value = ""
    db.commit()
    wp_routes.rollback_revision(rev["id"], db)

    plugin.set_term.assert_called_once_with(SITE, "tok", "subject", 52, meta_description="")
    plugin.set_post_seo.assert_not_called()
    assert db.get(models.SuggestionRevision, rev["id"]).rolled_back_at is not None


def test_term_rollback_without_a_recorded_taxonomy_is_refused(db, plugin):
    _, _, sug, conn = _seed(db, "https://site.com/subject/hindi/")
    plugin.post.return_value = _no_data()
    plugin.term.return_value = _ok({"taxonomy": "subject", "term_id": 52})
    plugin.set_term.return_value = _ok({"updated_fields": ["meta_description"]})  # plugin reply without "taxonomy"
    rev = _deploy(db, sug, conn, plugin)
    plugin.set_term.reset_mock()

    with pytest.raises(HTTPException) as e:
        wp_routes.rollback_revision(rev["id"], db)

    assert e.value.status_code == 400
    plugin.set_term.assert_not_called()  # cannot tell WHICH object to restore, so it doesn't guess
    plugin.set_post_seo.assert_not_called()
