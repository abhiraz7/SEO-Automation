"""
The optimizer's HTTP layer and the review page it renders: status codes, project
scoping, what the page shows for each kind of run, which buttons each state offers,
and that text from web pages or the model cannot inject markup. In-memory SQLite; the
AI and the search-evidence gathering are mocked, and a page view must never call either.
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.ai_errors import AIGenerationError
from app.database import get_db
from app.main import app
from app.services import content_optimizer as co
from tests.test_content_optimizer import KW, URL, ai_says, make_evidence, make_world, model_item


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
    co._running.clear()
    seed = Session()
    try:
        yield TestClient(app), seed
    finally:
        seed.close()
        app.dependency_overrides.pop(get_db, None)
        co._running.clear()
        engine.dispose()


def optimize(db, project, page, ai, **kw):
    with patch.object(co.ai_provider, "generate_optimizer_suggestions", side_effect=ai):
        return co.run_optimization(db, project, page.id, KW, "IN", "desktop", **kw)


def world(db, **page_over):
    project, page = make_world(db, **page_over)
    make_evidence(db, project, page)
    return project, page


def html_of(client, project, run):
    r = client.get(f"/projects/{project.id}/optimizer?run={run.id}")
    assert r.status_code == 200
    return r.text


def card_html(html, sid):
    """Just one suggestion card."""
    start = html.index(f'id="sug-{sid}"')
    end = html.index('id="sug-', start + 10) if 'id="sug-' in html[start + 10:] else len(html)
    return html[start:end]


TITLE = model_item("improve_title", "title", label="title does not contain", after="B.Ed admission: the complete guide for teachers", problem="The title omits the keyword.")


# ── the page ──────────────────────────────────────────────────────────────

def test_the_page_renders_with_no_runs_and_the_sidebar_links_to_it(env):
    client, db = env
    project, page = world(db)
    r = client.get(f"/projects/{project.id}/optimizer")
    assert r.status_code == 200
    assert "AI Content Optimizer" in r.text and 'name="page_id"' in r.text and 'name="keyword"' in r.text and "Find improvements" in r.text
    assert page.url in r.text and f"/projects/{project.id}/optimizer" in r.text and "Content Optimizer" in r.text
    assert "Recent runs" not in r.text.split("<script>")[0]
    assert "This run failed" not in r.text


def test_a_page_view_never_calls_a_provider_or_the_ai(env):
    client, db = env
    project, page = world(db)
    run = optimize(db, project, page, ai_says(model_item()))
    with patch.object(co.competitor_gap, "run_analysis") as gather, patch.object(co.ai_provider, "generate_optimizer_suggestions") as ai:
        assert client.get(f"/projects/{project.id}/optimizer").status_code == 200
        assert client.get(f"/projects/{project.id}/optimizer?run={run.id}").status_code == 200
        assert client.get(f"/projects/{project.id}/optimizer/runs/{run.id}").status_code == 200
    gather.assert_not_called()
    ai.assert_not_called()


def test_a_run_shows_each_suggestion_in_the_specified_layout(env):
    client, db = env
    project, page = world(db)
    run = optimize(db, project, page, ai_says(model_item(), TITLE))
    html = html_of(client, project, run)
    assert "2 suggestions to review" in html and "ACTION #1" in html and "ACTION #2" in html
    assert "Add a missing section: New section" in html and "Improve the title: Title" in html
    assert "4 of 5 comparable ranking pages cover “Documents required”. Your page: missing." in html
    assert "BEFORE" in html and "AI DRAFT" in html and "Teacher training course" in html and "B.Ed admission: the complete guide for teachers" in html
    assert "Nothing here yet: this is new content." in html
    assert "Show the changes" in html and "<ins" in html and "<del" in html
    assert "Validation" in html and "Complete suggestion" in html and "Duplication risk" in html and "Overlap with your other pages" in html
    assert "(a draft: nothing is published automatically)" in html
    assert "Evidence:" in html and "reused from a recent analysis" in html


def test_the_buttons_follow_the_state_of_each_suggestion(env):
    client, db = env
    project, page = world(db)
    run = optimize(db, project, page, ai_says(model_item(), TITLE))
    html = html_of(client, project, run)
    ids = {s.content[:5]: s.id for s in db.query(models.Suggestion)}
    section, title = card_html(html, ids["## Do"]), card_html(html, ids["B.Ed "])
    for card in (section, title):
        assert 'data-do="accept"' in card and 'data-do="edit-open"' in card and 'data-do="reject"' in card and 'data-do="deploy"' not in card
    assert "disabled" not in section.split('data-do="accept"')[1].split(">")[0].replace("data-needs-ack", "")     # ready to accept


def test_a_blocked_suggestion_is_shown_with_its_reasons_and_cannot_be_accepted_from_the_page(env):
    client, db = env
    project, page = world(db)
    # letters only: digits in the filler would be read as new figures, which disables Accept for a DIFFERENT reason
    filler = " ".join(f"term{chr(97 + i)}" for i in range(26))
    stuffed = "## Documents required\n\n" + " ".join(["b ed admission"] * 7) + " " + filler
    run = optimize(db, project, page, ai_says(model_item(after=stuffed)))
    sid = db.query(models.Suggestion).one().id
    card = card_html(html_of(client, project, run), sid)
    accept = card.split('data-do="accept"')[1].split(">")[0]
    assert "disabled" in accept and "Blocked by validation: fix or edit it first" in accept
    assert "✕" in card and "Keyword repetition" in card and "blocked" in card and 'data-do="edit-open"' in card and 'data-do="reject"' in card


def test_claims_that_need_verification_require_an_explicit_tick_before_accept_is_enabled(env):
    client, db = env
    project, page = world(db)
    run = optimize(db, project, page, ai_says(model_item(after="## Fees\n\nThe application fee is 5000 rupees for every applicant to the course each year.", claims_to_verify=["Fee amount"])))
    sid = db.query(models.Suggestion).one().id
    card = card_html(html_of(client, project, run), sid)
    accept = card.split('data-do="accept"')[1].split(">")[0]
    assert "disabled" in accept and 'data-needs-ack="1"' in accept
    assert 'class="opt-ack"' in card and "I checked the claims above" in card
    assert "Check these against a reliable source before approving:" in card and "Fee amount" in card and "?" in card


def test_only_deployable_categories_offer_deploy_and_others_explain_why(env):
    client, db = env
    project, page = world(db)
    run = optimize(db, project, page, ai_says(model_item(), TITLE))
    for s in db.query(models.Suggestion):
        s.status = "accepted"
    db.commit()
    html = html_of(client, project, run)
    ids = {s.content[:5]: s.id for s in db.query(models.Suggestion)}
    section, title = card_html(html, ids["## Do"]), card_html(html, ids["B.Ed "])
    assert 'data-do="deploy"' in title and "Deploy to WordPress" in title
    assert 'data-do="deploy"' not in section and "The WordPress connector can only write titles, meta descriptions and H1s." in section


def test_a_deployed_suggestion_shows_its_live_status_and_offers_recheck_and_rollback(env):
    client, db = env
    project, page = world(db)
    run = optimize(db, project, page, ai_says(TITLE))
    s = db.query(models.Suggestion).one()
    s.status = "deployed"
    rev = models.SuggestionRevision(suggestion_id=s.id, project_id=project.id, field_name="title", before_value="Old", after_value=s.content, wp_post_id=77,
                                    deployed_via="yoast_set_meta", verify_status="verified", verify_detail="the live page shows it")
    db.add(rev)
    db.commit()
    card = card_html(html_of(client, project, run), s.id)
    assert "live: live" in card and 'data-do="rollback"' in card and f'data-rid="{rev.id}"' in card and 'data-do="recheck"' in card
    assert "the live page shows it" in card and 'data-do="accept"' not in card and 'data-do="deploy"' not in card


def test_an_edited_suggestion_shows_your_version_and_keeps_the_original_visible(env):
    client, db = env
    project, page = world(db)
    run = optimize(db, project, page, ai_says(TITLE))
    s = db.query(models.Suggestion).one()
    s.status, s.edited_content = "edited", "B.Ed admission: my own better title for teachers"
    db.commit()
    card = card_html(html_of(client, project, run), s.id)
    assert "YOUR VERSION" in card and "my own better title for teachers" in card and "Original AI draft" in card and "the complete guide for teachers" in card


# ── every kind of outcome is stated as itself ─────────────────────────────

def test_a_failed_run_says_it_failed_and_why(env):
    client, db = env
    project, page = world(db)
    def boom(*a, **k):
        raise AIGenerationError("AI provider call failed: 529 overloaded")
    run = optimize(db, project, page, boom)
    html = html_of(client, project, run)
    assert "This run failed." in html and "529 overloaded" in html and "ACTION" not in html.split("<script>")[0]


def test_a_run_with_too_little_evidence_says_no_reliable_recommendation(env):
    client, db = env
    project, page = make_world(db)
    ev = make_evidence(db, project, page, status="no_data", error="Only 1 of 5 comparable pages could be analysed", with_gaps=False)
    run = optimize(db, project, page, ai_says(model_item()), evidence_run_id=ev.id)
    html = html_of(client, project, run)
    assert "No reliable optimization recommendation." in html and "Only 1 of 5" in html and "This run failed" not in html and "No change recommended." not in html


def test_a_search_provider_failure_is_shown_as_a_failure_not_as_no_change(env):
    client, db = env
    project, page = make_world(db)
    ev = make_evidence(db, project, page, status="error", error="The search results could not be fetched: dataforseo: 402 payment required", with_gaps=False)
    run = optimize(db, project, page, ai_says(model_item()), evidence_run_id=ev.id)
    html = html_of(client, project, run)
    assert "This run failed." in html and "402 payment required" in html and "No change recommended." not in html


def test_no_change_is_shown_as_a_real_answer(env):
    client, db = env
    project, page = world(db)
    run = optimize(db, project, page, ai_says(no_change_reason="No change recommended. The page already answers the recurring questions."))
    html = html_of(client, project, run)
    assert "No change recommended." in html and "already answers the recurring questions" in html and "This run failed" not in html


def test_discarded_proposals_are_listed_with_their_reasons(env):
    client, db = env
    project, page = world(db)
    run = optimize(db, project, page, ai_says(model_item("rewrite_entire_article"), model_item()))
    html = html_of(client, project, run)
    assert "1 AI proposal not used" in html and "unsupported suggestion type" in html


def test_a_semrush_fallback_run_shows_the_evidence_notice(env):
    client, db = env
    project, page = make_world(db)
    make_evidence(db, project, page, source="semrush")
    run = optimize(db, project, page, ai_says(model_item()))
    assert "fell back to Semrush" in html_of(client, project, run)


def test_earlier_decisions_stay_visible_and_recent_runs_are_listed(env):
    client, db = env
    project, page = world(db)
    optimize(db, project, page, ai_says(model_item(after="## Documents required\n\nThe first proposal that a person accepted about certificates and photographs.")))
    db.query(models.Suggestion).one().status = "accepted"
    db.commit()
    second = optimize(db, project, page, ai_says(model_item(after="## Documents required\n\nA second and different proposal about the certificates that applicants carry.")))
    html = html_of(client, project, second)
    assert "Earlier decisions on this page" in html and "The first proposal that a person accepted" in html and "Recent runs</div>" in html


# ── hostile text ──────────────────────────────────────────────────────────

def test_text_from_pages_and_the_model_is_escaped_everywhere(env):
    client, db = env
    project, page = make_world(db, title="<script>alert('t')</script> Title")
    make_evidence(db, project, page)
    evil = "<img src=x onerror=alert(1)>"
    run = optimize(db, project, page, ai_says(
        model_item("improve_title", "title", label="title does not contain", after="B.Ed admission guide " + evil, problem="Problem " + evil, claims_to_verify=[evil])))
    html = html_of(client, project, run)
    for raw in ("<script>alert('t')</script>", evil):
        assert raw not in html
    assert "&lt;script&gt;" in html and "&lt;img" in html
    body = html.split("<script>")[0]
    assert "onerror=alert(1)>" not in body


def test_the_detail_json_is_serialisable_and_carries_only_escaped_diff_html(env):
    client, db = env
    project, page = world(db)
    run = optimize(db, project, page, ai_says(TITLE))
    d = client.get(f"/projects/{project.id}/optimizer/runs/{run.id}").json()
    s = d["suggestions"][0]
    assert d["status"] == "ok" and s["type"] == "improve_title" and isinstance(s["diff_html"], str) and "<ins" in s["diff_html"]
    assert s["validation"]["checks"][0]["name"] == "schema" and s["deployable"] is True and s["evidence"][0]["text"]


# ── scoping ───────────────────────────────────────────────────────────────

def test_a_run_is_only_reachable_through_its_own_project(env):
    client, db = env
    mine, page = world(db)
    other = models.Project(name="Other", base_url="https://other.com")
    db.add(other)
    db.commit()
    run = optimize(db, mine, page, ai_says(model_item()))
    assert client.get(f"/projects/{other.id}/optimizer?run={run.id}").status_code == 404
    assert client.get(f"/projects/{other.id}/optimizer/runs/{run.id}").status_code == 404
    assert "Documents required" not in client.get(f"/projects/{other.id}/optimizer").text


def test_unknown_project_or_run_is_404(env):
    client, db = env
    project, _ = world(db)
    assert client.get(f"/projects/{project.id}/optimizer?run=9999").status_code == 404
    assert client.get("/projects/9999/optimizer").status_code == 404
    assert client.post("/projects/9999/optimizer/run", data={"page_id": 1}).status_code == 404


# ── the two-step flow ─────────────────────────────────────────────────────

FORM = {"page_id": "", "keyword": KW, "location": "IN", "device": "desktop"}


def test_step_one_reuses_a_fresh_analysis_without_gathering(env):
    client, db = env
    project, page = world(db)
    with patch.object(co.competitor_gap, "run_analysis") as gather:
        r = client.post(f"/projects/{project.id}/optimizer/evidence", data={**FORM, "page_id": page.id})
    gather.assert_not_called()
    body = r.json()
    assert r.status_code == 200 and body["reused"] is True and body["status"] == "ok" and body["summary_line"] == "5 of 5 comparable competitors successfully analysed"


def test_step_one_gathers_evidence_only_when_there_is_none_and_never_pays_for_the_ai_plan(env):
    client, db = env
    project, page = make_world(db)
    seen = {}
    def gather(db_, project_, url, kw, loc, dev, with_plan=True):
        seen["with_plan"] = with_plan
        return make_evidence(db_, project_, page)
    with patch.object(co.competitor_gap, "run_analysis", side_effect=gather):
        r = client.post(f"/projects/{project.id}/optimizer/evidence", data={**FORM, "page_id": page.id})
    assert r.status_code == 200 and r.json()["reused"] is False and seen == {"with_plan": False}


def test_step_one_with_refresh_gathers_even_when_a_fresh_analysis_exists(env):
    client, db = env
    project, page = world(db)
    with patch.object(co.competitor_gap, "run_analysis", side_effect=lambda *a, **k: make_evidence(db, project, page)) as gather:
        r = client.post(f"/projects/{project.id}/optimizer/evidence", data={**FORM, "page_id": page.id, "refresh": "on"})
    gather.assert_called_once()
    assert r.json()["reused"] is False


def test_a_failed_search_at_step_one_is_still_a_200_that_step_two_records(env):
    client, db = env
    project, page = make_world(db)
    bad = make_evidence(db, project, page, status="error", error="dataforseo: 402", with_gaps=False)
    with patch.object(co.competitor_gap, "run_analysis", return_value=bad):
        r = client.post(f"/projects/{project.id}/optimizer/evidence", data={**FORM, "page_id": page.id, "refresh": "on"})
    assert r.status_code == 200 and r.json()["status"] == "error" and "402" in r.json()["error"]
    with patch.object(co.ai_provider, "generate_optimizer_suggestions") as ai:
        r2 = client.post(f"/projects/{project.id}/optimizer/run", data={**FORM, "page_id": page.id, "evidence_run_id": bad.id})
    ai.assert_not_called()
    assert r2.status_code == 200 and r2.json()["status"] == "error" and "402" in r2.json()["error"]


def test_the_whole_flow_over_http(env):
    client, db = env
    project, page = world(db)
    ev = client.post(f"/projects/{project.id}/optimizer/evidence", data={**FORM, "page_id": page.id}).json()
    with patch.object(co.ai_provider, "generate_optimizer_suggestions", side_effect=ai_says(model_item(), TITLE)):
        out = client.post(f"/projects/{project.id}/optimizer/run", data={**FORM, "page_id": page.id, "evidence_run_id": ev["evidence_run_id"]}).json()
    assert out["status"] == "ok" and out["url"] == f"/projects/{project.id}/optimizer?run={out['run_id']}"
    assert "2 suggestions to review" in client.get(out["url"]).text


@pytest.mark.parametrize("override,status,fragment", [
    ({"keyword": "x"}, 400, "target keyword"),
    ({"location": "ZZ"}, 400, "Unsupported location"),
    ({"device": "tablet"}, 400, "desktop or mobile"),
    ({"page_id": "9999"}, 404, "page was not found"),
    ({"page_id": "0"}, 404, "page was not found"),
])
def test_bad_input_maps_to_400_or_404_with_a_readable_reason_and_calls_nothing(env, override, status, fragment):
    client, db = env
    project, page = world(db)
    body = {**FORM, "page_id": page.id, **override}
    with patch.object(co.competitor_gap, "run_analysis") as gather, patch.object(co.ai_provider, "generate_optimizer_suggestions") as ai:
        r1 = client.post(f"/projects/{project.id}/optimizer/evidence", data=body)
        r2 = client.post(f"/projects/{project.id}/optimizer/run", data=body)
    assert r1.status_code == r2.status_code == status and fragment in r1.json()["detail"] and fragment in r2.json()["detail"]
    gather.assert_not_called()
    ai.assert_not_called()
    assert db.query(models.ContentOptimizationRun).count() == 0


def test_a_page_from_another_project_is_a_404(env):
    client, db = env
    mine, _ = world(db)
    other = models.Project(name="Other", base_url="https://other.com")
    db.add(other)
    db.flush()
    foreign = models.Page(project_id=other.id, url="https://other.com/x", source="crawler")
    db.add(foreign)
    db.commit()
    r = client.post(f"/projects/{mine.id}/optimizer/run", data={**FORM, "page_id": foreign.id})
    assert r.status_code == 404


def test_an_analysis_already_running_is_a_409_at_both_steps(env):
    client, db = env
    project, page = make_world(db)
    with patch.object(co.competitor_gap, "run_analysis", side_effect=co.BusyError("An analysis is already running for this project.")):
        r1 = client.post(f"/projects/{project.id}/optimizer/evidence", data={**FORM, "page_id": page.id})
        r2 = client.post(f"/projects/{project.id}/optimizer/run", data={**FORM, "page_id": page.id})
    assert r1.status_code == r2.status_code == 409 and "already running" in r1.json()["detail"]
    co._running.add((project.id, page.id))
    assert client.post(f"/projects/{project.id}/optimizer/run", data={**FORM, "page_id": page.id}).status_code == 409


def test_a_given_analysis_for_something_else_is_a_400(env):
    client, db = env
    project, page = world(db)
    other = make_evidence(db, project, page, keyword="a different keyword")
    r = client.post(f"/projects/{project.id}/optimizer/run", data={**FORM, "page_id": page.id, "evidence_run_id": other.id})
    assert r.status_code == 400 and "different page, keyword" in r.json()["detail"]
