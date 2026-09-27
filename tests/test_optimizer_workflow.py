"""
Accept / edit / reject / deploy / rollback for AI Content Optimizer suggestions,
through the EXISTING routes. The rule under test: a suggestion the validation blocked
cannot be approved, edited into approval, or deployed, enforced on the server; and every
non-optimizer suggestion behaves exactly as before. In-memory SQLite; the WordPress
plugin calls are mocked (same approach as test_term_deploy.py); no network.
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models, wordpress
from app.routes import suggestions as sr
from app.routes import wordpress as wp_routes
from app.services import content_optimizer as co

SITE = "https://site.com"


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


OK_CHECKS = [{"name": n, "status": "ok"} for n in ("schema", "structure", "keyword_repetition", "duplication", "competitor_copy", "fact_check", "brand_tone")]
BLOCKED_CHECKS = [{"name": "duplication", "status": "blocked", "message": "Duplication risk: about 90% of this text is already on the page."}] + OK_CHECKS[:1]


def make(db, type_="improve_title", category="title", rule="opt_improve_title", target="title", before="Old title", content="New title for the page",
         status="pending", validation_status="ok", checks=None, project=None, page=None):
    if project is None:
        project = models.Project(name="P", base_url=SITE)
        db.add(project)
        db.flush()
        page = models.Page(project_id=project.id, url="https://site.com/some-post/", wp_post_id=77, title="Old title", source="crawler")
        db.add(page)
        db.flush()
    issue = db.query(models.Issue).filter_by(page_id=page.id, category=category, rule=rule).first()
    if issue is None:
        issue = models.Issue(project_id=project.id, page_id=page.id, category=category, rule=rule, severity="info", message="AI Content Optimizer")
        db.add(issue)
        db.flush()
    run = models.ContentOptimizationRun(project_id=project.id, page_id=page.id, target_url=page.url, keyword="b ed admission", location="IN", device="desktop", status="ok")
    db.add(run)
    db.flush()
    s = models.Suggestion(project_id=project.id, page_id=page.id, issue_id=issue.id, content=content, content_hash=co.content_hash(content), status=status)
    db.add(s)
    db.flush()
    db.add(models.SuggestionOptimization(
        suggestion_id=s.id, run_id=run.id, project_id=project.id, page_id=page.id, suggestion_type=type_, target_ref=target, target_label="Title",
        priority="high", problem="The title omits the keyword.", evidence_json=[{"type": "page_fact", "label": "The title does not contain the target keyword", "competitor_count": 0, "competitor_total": 0}],
        before_content=before, confidence="high", validation_status=validation_status,
        validation_json={"status": validation_status, "checks": checks if checks is not None else OK_CHECKS}))
    db.commit()
    return project, page, s


def meta_of(db, s):
    return db.query(models.SuggestionOptimization).filter_by(suggestion_id=s.id).one()


# ── accept ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("vs", ["ok", "warning", "needs_human_verification"])
def test_a_suggestion_that_is_ready_for_approval_can_be_accepted(db, vs):
    _, _, s = make(db, validation_status=vs)
    out = sr.accept_suggestion(s.id, db)
    assert out["status"] == "accepted" and db.get(models.Suggestion, s.id).accepted_at is not None


@pytest.mark.parametrize("vs", ["blocked", "error"])
def test_a_blocked_or_errored_suggestion_cannot_be_accepted_and_nothing_changes(db, vs):
    checks = BLOCKED_CHECKS if vs == "blocked" else [{"name": "fact_check", "status": "error", "message": "This check could not run: boom"}]
    _, _, s = make(db, validation_status=vs, checks=checks)
    with pytest.raises(HTTPException) as e:
        sr.accept_suggestion(s.id, db)
    assert e.value.status_code == 409 and "Blocked by validation" in e.value.detail
    assert ("Duplication risk" in e.value.detail) if vs == "blocked" else ("could not run" in e.value.detail)
    row = db.get(models.Suggestion, s.id)
    assert row.status == "pending" and row.accepted_at is None


def test_a_blocked_suggestion_can_still_be_rejected(db):
    _, _, s = make(db, validation_status="blocked", checks=BLOCKED_CHECKS)
    assert sr.reject_suggestion(s.id, db)["status"] == "rejected"


def test_a_deployed_suggestion_still_says_roll_back_first_and_a_missing_one_is_404(db):
    _, _, s = make(db, status="deployed")
    with pytest.raises(HTTPException) as e:
        sr.accept_suggestion(s.id, db)
    assert e.value.status_code == 409 and "roll it back" in e.value.detail
    with pytest.raises(HTTPException) as e2:
        sr.accept_suggestion(9999, db)
    assert e2.value.status_code == 404


# ── edit ──────────────────────────────────────────────────────────────────

def test_a_valid_edit_is_saved_and_its_validation_replaces_the_old_one(db):
    _, _, s = make(db, validation_status="blocked", checks=BLOCKED_CHECKS)
    out = sr.edit_suggestion(s.id, sr.SuggestionEditIn(content="  B.Ed admission: a complete guide for teachers  "), db)
    assert out["status"] == "edited" and out["edited_content"] == "B.Ed admission: a complete guide for teachers"
    row = db.get(models.Suggestion, s.id)
    assert row.content == "New title for the page"                        # the AI's original is never overwritten
    m = meta_of(db, s)
    assert m.validation_status != "blocked" and [c["name"] for c in m.validation_json["checks"]][:2] == ["schema", "structure"]
    assert len(m.validation_json["checks"]) == 7                          # a real, fresh run of the pipeline, not the old stored result
    assert sr.accept_suggestion(s.id, db)["status"] == "accepted"         # an edit that fixed it can now be approved


def test_a_blocked_edit_is_refused_saves_nothing_and_says_why(db):
    _, _, s = make(db)
    with pytest.raises(HTTPException) as e:
        sr.edit_suggestion(s.id, sr.SuggestionEditIn(content="b ed admission | b ed admission | b ed admission"), db)
    assert e.value.status_code == 422 and "not saved" in e.value.detail and "keyword_repetition" in e.value.detail
    row = db.get(models.Suggestion, s.id)
    assert row.status == "pending" and row.edited_content is None and row.accepted_at is None
    assert meta_of(db, s).validation_status == "ok"                       # the stored validation still describes the stored text


def test_an_edit_that_adds_a_figure_is_allowed_but_flagged_for_verification(db):
    _, _, s = make(db)
    sr.edit_suggestion(s.id, sr.SuggestionEditIn(content="B.Ed admission 2026: the complete guide for teachers"), db)
    m = meta_of(db, s)
    assert m.validation_status == "needs_human_verification" and m.requires_fact_check is True and any("2026" in c for c in m.claims_to_verify)


def test_editing_away_a_flagged_claim_clears_the_flag(db):
    _, _, s = make(db, content="B.Ed admission 2026: the complete guide", validation_status="needs_human_verification")
    m = meta_of(db, s)
    m.requires_fact_check, m.claims_to_verify = True, ["B.Ed admission 2026: the complete guide"]
    db.commit()
    sr.edit_suggestion(s.id, sr.SuggestionEditIn(content="B.Ed admission: the complete guide for teachers"), db)
    m = meta_of(db, s)
    assert m.requires_fact_check is False and m.claims_to_verify == [] and m.validation_status == "ok"


def test_edit_keeps_its_existing_guards_empty_text_and_deployed(db):
    _, _, s = make(db)
    with pytest.raises(HTTPException) as e:
        sr.edit_suggestion(s.id, sr.SuggestionEditIn(content="   "), db)
    assert e.value.status_code == 400
    _, _, d = make(db, status="deployed")
    with pytest.raises(HTTPException) as e2:
        sr.edit_suggestion(d.id, sr.SuggestionEditIn(content="Something else entirely here"), db)
    assert e2.value.status_code == 409


# ── legacy suggestions are untouched ──────────────────────────────────────

def test_a_legacy_suggestion_is_accepted_and_edited_exactly_as_before(db):
    project = models.Project(name="P", base_url=SITE)
    db.add(project)
    db.flush()
    page = models.Page(project_id=project.id, url="https://site.com/x/")
    db.add(page)
    db.flush()
    issue = models.Issue(project_id=project.id, page_id=page.id, category="title", rule="too_short", message="m")
    db.add(issue)
    db.flush()
    s = models.Suggestion(project_id=project.id, page_id=page.id, issue_id=issue.id, content="Legacy title", content_hash="h", status="pending")
    db.add(s)
    db.commit()
    with patch.object(co, "revalidate", wraps=co.revalidate) as reval, patch.object(co, "apply_validation") as apply:
        assert sr.accept_suggestion(s.id, db)["status"] == "accepted"
        out = sr.edit_suggestion(s.id, sr.SuggestionEditIn(content="A different legacy title"), db)
    assert out["status"] == "edited" and out["edited_content"] == "A different legacy title"
    reval.assert_called_once()
    apply.assert_not_called()                       # no optimizer record, so nothing was validated or stored
    assert db.query(models.SuggestionOptimization).count() == 0


# ── deploy and rollback (existing routes, WordPress mocked) ───────────────

def ok(data):
    return wordpress.WordPressResult(status="ok", data=data)


@pytest.fixture
def plugin():
    with patch.object(wp_routes, "_connected_or_error") as conn, \
         patch.object(wordpress, "get_yoast_meta") as get_meta, \
         patch.object(wordpress, "set_yoast_meta") as set_meta, \
         patch.object(wordpress, "update_post_content") as update_post:
        get_meta.return_value = ok({"seo_title": "Live title on the site"})
        set_meta.return_value = ok({})
        update_post.return_value = ok({})
        conn.return_value = (models.WordPressConnection(project_id=1, site_url=SITE, api_token="x", last_verify_ok=True), "tok")
        yield MagicMock(get_meta=get_meta, set_meta=set_meta, update_post=update_post)


def deploy(db, s):
    return wp_routes.deploy_suggestion(s.id, wp_routes.DeployIn(wp_post_id=None), db)


def test_an_approved_optimizer_title_deploys_through_the_existing_route(db, plugin):
    _, _, s = make(db, status="accepted")
    rev = deploy(db, s)
    plugin.set_meta.assert_called_once_with(SITE, "tok", 77, seo_title="New title for the page")
    assert rev["before_value"] == "Live title on the site" and rev["after_value"] == "New title for the page"
    assert db.get(models.Suggestion, s.id).status == "deployed"
    jobs = db.query(models.Job).filter_by(job_type="verify_deploy").all()
    assert len(jobs) == 1 and jobs[0].payload == {"revision_id": rev["id"]}       # the existing live verification is queued


def test_a_person_s_edited_text_is_what_gets_deployed(db, plugin):
    _, _, s = make(db)
    sr.edit_suggestion(s.id, sr.SuggestionEditIn(content="B.Ed admission: a complete guide for teachers"), db)
    deploy(db, s)
    plugin.set_meta.assert_called_once_with(SITE, "tok", 77, seo_title="B.Ed admission: a complete guide for teachers")


def test_a_blocked_suggestion_is_never_written_even_if_its_status_was_forced_to_accepted(db, plugin):
    """Defense in depth: accept refuses, but deploy is the step that changes a live site."""
    _, _, s = make(db, status="accepted", validation_status="blocked", checks=BLOCKED_CHECKS)
    with pytest.raises(HTTPException) as e:
        deploy(db, s)
    assert e.value.status_code == 409 and "Blocked by validation" in e.value.detail and "was not deployed" in e.value.detail
    plugin.set_meta.assert_not_called()
    plugin.update_post.assert_not_called()
    assert db.query(models.SuggestionRevision).count() == 0 and db.get(models.Suggestion, s.id).status == "accepted"


def test_content_level_suggestions_cannot_be_deployed_only_approved(db, plugin):
    _, _, s = make(db, type_="add_section", category="content", rule="opt_add_section", target="new", before=None,
                   content="## Documents required\n\nBring your degree certificate and photo identity to the counter.", status="accepted")
    with pytest.raises(HTTPException) as e:
        deploy(db, s)
    assert e.value.status_code == 400 and "No deploy support yet for field type 'content'" in e.value.detail
    plugin.set_meta.assert_not_called()
    plugin.update_post.assert_not_called()
    assert db.get(models.Suggestion, s.id).status == "accepted"                     # stays approved, ready for a person to apply


def test_an_h2_heading_suggestion_cannot_be_deployed_either(db, plugin):
    _, _, s = make(db, type_="improve_heading", category="h2", rule="opt_improve_heading", target="sec_02", content="Fee structure", status="accepted")
    with pytest.raises(HTTPException) as e:
        deploy(db, s)
    assert e.value.status_code == 400 and "'h2'" in e.value.detail


def test_an_optimizer_deploy_can_be_rolled_back_to_the_live_value(db, plugin):
    _, _, s = make(db, status="accepted")
    rev = deploy(db, s)
    plugin.set_meta.reset_mock()
    wp_routes.rollback_revision(rev["id"], db)
    plugin.set_meta.assert_called_once_with(SITE, "tok", 77, seo_title="Live title on the site")    # the value read from the live site before writing
    assert db.get(models.SuggestionRevision, rev["id"]).rolled_back_at is not None
    assert db.get(models.Suggestion, s.id).status == "accepted"                                      # the decision stands; only the live change is undone
    assert meta_of(db, s).validation_status == "ok"


def test_a_deployed_optimizer_suggestion_survives_an_audit_and_stays_rollbackable(db, plugin):
    """The reason the 'opt_' protections exist: the audit that follows every crawl must not orphan a live change."""
    from app.routes import audit as audit_route
    project, page, s = make(db, status="accepted")
    rev = deploy(db, s)
    audit_route._persist_issues(db, project.id, {page.id: [{"category": "canonical", "rule": "missing", "severity": "warning", "message": "no canonical"}]})
    assert db.get(models.Suggestion, s.id).status == "deployed" and db.get(models.Issue, s.issue_id) is not None
    plugin.set_meta.reset_mock()
    wp_routes.rollback_revision(rev["id"], db)
    plugin.set_meta.assert_called_once()


def test_legacy_deploy_is_unchanged(db, plugin):
    project = models.Project(name="P", base_url=SITE)
    db.add(project)
    db.flush()
    page = models.Page(project_id=project.id, url="https://site.com/some-post/", wp_post_id=77)
    db.add(page)
    db.flush()
    issue = models.Issue(project_id=project.id, page_id=page.id, category="title", rule="too_short", message="m")
    db.add(issue)
    db.flush()
    s = models.Suggestion(project_id=project.id, page_id=page.id, issue_id=issue.id, content="Legacy title", content_hash="h", status="accepted")
    db.add(s)
    db.commit()
    rev = deploy(db, s)
    plugin.set_meta.assert_called_once_with(SITE, "tok", 77, seo_title="Legacy title")
    assert rev["after_value"] == "Legacy title"
