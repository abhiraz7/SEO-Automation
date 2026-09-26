"""
The competitor-gap HTTP layer and the page it renders: status codes, project scoping,
what the page shows for each kind of run (including failures), and that third-party
text (competitor titles, headings, URLs) cannot inject markup. In-memory SQLite;
every provider and AI call is mocked, and a page view must never make one.
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
from app.services import competitor_gap as cg
from app.services import serp_evidence as se


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
    cg._running.clear()
    cg._drafting.clear()
    seed = Session()
    try:
        yield TestClient(app), seed
    finally:
        seed.close()
        app.dependency_overrides.pop(get_db, None)
        cg._running.clear()
        cg._drafting.clear()
        engine.dispose()


TARGET = "https://mine.com/b-ed-admission"

ACTION = {
    "id": "action_001", "type": "add", "priority": "high", "title": "Add an eligibility section", "problem": "Your page never says who can apply.",
    "recommendation": "Add a section explaining who is eligible.", "confidence": "high", "requires_fact_check": False,
    "evidence": [{"type": "topic_consensus", "label": "Eligibility criteria", "competitor_count": 4, "competitor_total": 5, "gap_id": None}],
}


def make_project(db, name="P", base="https://mine.com"):
    project = models.Project(name=name, base_url=base)
    db.add(project)
    db.commit()
    return project


def seed_run(db, project, status="ok", plan_status="ok", error=None, plan_error=None, actions=None, snapshot_overrides=None,
             gap_label="Eligibility criteria", keyword="b ed admission", source="dataforseo", analyzed=5, selected=5):
    run = models.CompetitorAnalysisRun(
        project_id=project.id, target_url=TARGET, keyword=keyword, location="IN", device="desktop", status=status, error=error,
        source=source, serp_features=["people_also_ask"], serp_total_results=5, target_snapshot={"url": TARGET, "title": "B.Ed", "h1": "B.Ed", "headings": [], "word_count": 100, "excerpt": "x", "source": "test"},
        intent={"label": "informational", "confidence": "high", "signals": ["how"]}, format_distribution={"guide": 5},
        competitors_selected=selected, competitors_analyzed=analyzed, plan_status=plan_status, plan_error=plan_error,
        action_plan_json={"actions": actions if actions is not None else [dict(ACTION)], "rejected": [{"id": "x1", "reason": "cites no evidence"}], "warnings": ["a warning"]} if plan_status == "ok" else None,
    )
    db.add(run)
    db.flush()
    snap = dict(url="https://rival1.com/guide", position=1, result_class="direct_content", selected=True, title="Guide 1", h1="Guide",
                headings_json=[], text="body", word_count=800, fetch_method="http", fetch_status="ok", extraction_confidence="high")
    snap.update(snapshot_overrides or {})
    db.add(models.CompetitorPageSnapshot(analysis_run_id=run.id, **snap))
    db.add(models.CompetitorPageSnapshot(analysis_run_id=run.id, url="https://www.reddit.com/r/x", position=2, result_class="forum", selected=False,
                                         title="Reddit thread", fetch_status="skipped", error="a forum thread, not a comparable page"))
    db.add(models.CompetitorPageSnapshot(analysis_run_id=run.id, url="https://rival3.com/g", position=3, result_class="direct_content", selected=True,
                                         title="Guide 3", fetch_status="error", error="blocked (HTTP 403: login, paywall or access control)"))
    db.add(models.CompetitorGap(analysis_run_id=run.id, gap_type="topic", label=gap_label, target_coverage="missing", competitor_count=4, competitor_total=5,
                                evidence_json={"competitors": [{"position": 1, "domain": "rival1.com", "url": "https://rival1.com/guide", "heading": gap_label}]},
                                confidence="high", recommended_action="add"))
    db.commit()
    return run


def card(html: str) -> str:
    """Just the gap-analysis card (up to the existing 'My Website' section)."""
    start = html.index('id="gap-card"')
    return html[start:html.index("My Website")]


# ── page ──────────────────────────────────────────────────────────────────

def test_the_page_renders_with_no_analyses_yet(env):
    client, db = env
    p = make_project(db)
    r = client.get(f"/projects/{p.id}/competitors")
    assert r.status_code == 200
    assert "AI Competitor Gap Analysis" in r.text and 'name="keyword"' in r.text and 'name="location"' in r.text
    assert "Recent analyses</div>" not in r.text
    assert "My Website" in r.text and "Overview Comparison" in r.text          # the existing sections are still there


def test_a_page_view_never_calls_a_provider_or_the_ai(env):
    client, db = env
    p = make_project(db)
    run = seed_run(db, p)
    with patch.object(cg.serp_evidence, "fetch_serp_evidence") as s, patch.object(cg.page_evidence, "fetch_pages_evidence") as f, \
         patch.object(cg.page_evidence, "fetch_page_evidence") as one, patch.object(cg.ai_provider, "generate_action_plan") as a, \
         patch.object(cg.ai_provider, "generate_gap_draft") as d:
        assert client.get(f"/projects/{p.id}/competitors").status_code == 200
        assert client.get(f"/projects/{p.id}/competitors?run={run.id}").status_code == 200
        assert client.get(f"/projects/{p.id}/competitors/gap-analysis/{run.id}").status_code == 200
    for m in (s, f, one, a, d):
        m.assert_not_called()


def test_a_complete_run_shows_the_summary_gaps_actions_and_every_competitor(env):
    client, db = env
    p = make_project(db)
    run = seed_run(db, p, analyzed=4, selected=5)
    html = card(client.get(f"/projects/{p.id}/competitors?run={run.id}").text)
    assert "4 of 5 comparable competitors successfully analysed" in html
    assert "Your page: missing" in html and "4 of 5 competitors" in html and "Eligibility criteria" in html
    assert "Add an eligibility section" in html and "high priority" in html and "Draft this" in html
    assert "1 AI suggestion discarded" in html and "1 adjustment made" in html
    assert "Recent analyses</div>" in html and "informational" in html
    # the page table shows the excluded and the failed competitors with their reasons
    assert "a forum thread, not a comparable page" in html and "blocked (HTTP 403" in html and "rival1.com" in html


def test_there_is_no_score_anywhere_on_the_card(env):
    client, db = env
    p = make_project(db)
    run = seed_run(db, p)
    assert "score" not in card(client.get(f"/projects/{p.id}/competitors?run={run.id}").text).lower()


def test_a_failed_run_says_it_failed_and_shows_no_actions(env):
    client, db = env
    p = make_project(db)
    run = models.CompetitorAnalysisRun(project_id=p.id, target_url=TARGET, keyword="kw one", location="IN", device="desktop", status="error",
                                       error="The search results could not be fetched: dataforseo: 402 payment required", plan_status="not_run")
    db.add(run)
    db.commit()
    html = card(client.get(f"/projects/{p.id}/competitors?run={run.id}").text)
    assert "This analysis failed." in html and "402 payment required" in html
    assert "Recommended actions" not in html and "comparable competitors successfully analysed" not in html


def test_a_no_data_run_is_not_shown_as_a_success(env):
    client, db = env
    p = make_project(db)
    run = models.CompetitorAnalysisRun(project_id=p.id, target_url=TARGET, keyword="kw two", location="IN", device="desktop", status="no_data",
                                       error="Only 1 of 5 comparable pages could be analysed", plan_status="not_run")
    db.add(run)
    db.commit()
    html = card(client.get(f"/projects/{p.id}/competitors?run={run.id}").text)
    assert "Not enough data to analyse." in html and "Only 1 of 5" in html and "Recommended actions" not in html


def test_an_ai_plan_failure_is_shown_as_the_plans_failure_and_the_evidence_stays(env):
    client, db = env
    p = make_project(db)
    run = seed_run(db, p, plan_status="error", plan_error="AI provider call failed: 529")
    html = card(client.get(f"/projects/{p.id}/competitors?run={run.id}").text)
    assert "The AI action plan could not be generated." in html and "529" in html
    assert "Eligibility criteria" in html and "Your page: missing" in html


def test_a_semrush_fallback_run_shows_its_notice(env):
    client, db = env
    p = make_project(db)
    run = seed_run(db, p, source="semrush")
    assert "fell back to Semrush" in card(client.get(f"/projects/{p.id}/competitors?run={run.id}").text)


def test_draft_states_pending_accepted_edited_rejected(env):
    client, db = env
    p = make_project(db)
    cases = {
        "pending": ("awaiting your decision", ["Accept", "Reject", "Edit"]),
        "accepted": ("accepted", []),
        "edited": ("edited by you", []),
        "rejected": ("rejected", ["Generate a new draft"]),
    }
    for status, (label, buttons) in cases.items():
        draft = {"text": "AI wrote this.", "status": status, "claims_to_verify": ["It costs 500 rupees."], "warnings": ["1 statement(s) need checking"]}
        if status == "edited":
            draft["edited_text"] = "The human wrote this."
        run = seed_run(db, p, actions=[{**ACTION, "draft": draft}], keyword=f"kw {status}")
        html = card(client.get(f"/projects/{p.id}/competitors?run={run.id}").text)
        assert label in html and "It costs 500 rupees." in html and "Verify before using" in html, status
        for b in buttons:
            assert b in html, (status, b)
        if status == "edited":
            assert "The human wrote this." in html and "Original AI draft" in html
        if status in ("accepted", "edited"):
            assert 'data-do="accept"' not in html and 'data-do="draft"' not in html    # decided drafts are never regenerated from the UI


def test_non_text_actions_get_no_draft_button(env):
    client, db = env
    p = make_project(db)
    run = seed_run(db, p, actions=[{**ACTION, "type": "separate_page"}])
    html = card(client.get(f"/projects/{p.id}/competitors?run={run.id}").text)
    assert 'data-do="draft"' not in html and "no draft text is produced" in html


# ── third-party text cannot inject anything ───────────────────────────────

def test_hostile_competitor_text_is_escaped_and_bad_urls_are_not_links(env):
    client, db = env
    p = make_project(db)
    run = seed_run(
        db, p, gap_label='<img src=x onerror=alert(1)>',
        snapshot_overrides={"title": "<script>alert(1)</script>", "url": "javascript:alert(1)", "error": '"><b>x</b>'},
    )
    html = client.get(f"/projects/{p.id}/competitors?run={run.id}").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<img src=x onerror=alert(1)>" not in html and "&lt;img src=x onerror=alert(1)&gt;" in html
    assert 'href="javascript:' not in html.lower()
    assert '"><b>x</b>' not in html


def test_a_draft_written_by_the_ai_is_escaped_too(env):
    client, db = env
    p = make_project(db)
    draft = {"text": "<script>steal()</script>", "status": "pending", "claims_to_verify": [], "warnings": []}
    run = seed_run(db, p, actions=[{**ACTION, "draft": draft}])
    html = client.get(f"/projects/{p.id}/competitors?run={run.id}").text
    assert "<script>steal()</script>" not in html and "&lt;script&gt;steal()&lt;/script&gt;" in html


# ── project scoping ───────────────────────────────────────────────────────

def test_a_run_is_only_reachable_through_its_own_project(env):
    client, db = env
    mine = make_project(db, "Mine")
    theirs = make_project(db, "Theirs", "https://theirs.com")
    run = seed_run(db, theirs)
    for method, url in [
        ("get", f"/projects/{mine.id}/competitors?run={run.id}"),
        ("get", f"/projects/{mine.id}/competitors/gap-analysis/{run.id}"),
        ("post", f"/projects/{mine.id}/competitors/gap-analysis/{run.id}/actions/action_001/draft"),
        ("post", f"/projects/{mine.id}/competitors/gap-analysis/{run.id}/actions/action_001/decision"),
    ]:
        assert getattr(client, method)(url).status_code == 404, url
    assert client.get(f"/projects/{mine.id}/competitors").text.count("Eligibility criteria") == 0    # and it is not in the history list


def test_an_unknown_run_or_project_is_404(env):
    client, db = env
    p = make_project(db)
    assert client.get(f"/projects/{p.id}/competitors?run=9999").status_code == 404
    assert client.get(f"/projects/9999/competitors/gap-analysis/1").status_code == 404
    assert client.post("/projects/9999/competitors/gap-analysis", data={"target_url": TARGET}).status_code == 404


# ── start an analysis ─────────────────────────────────────────────────────

def _serp():
    return se.normalize_serp({"items": [{"type": "organic", "rank_group": i, "url": f"https://rival{i}.com/g", "title": f"T{i}"} for i in range(1, 4)], "_source": "dataforseo"})


def _page(headings):
    return {"status": "ok", "fetch_method": "http", "title": "t", "h1": "t", "headings": [{"tag": "h2", "text": h} for h in headings],
            "text": "body " * 200, "word_count": 400, "questions": [], "extraction_confidence": "high", "error": None}


FORM = {"target_url": TARGET, "keyword": "b ed admission", "location": "IN", "device": "desktop"}


def test_starting_an_analysis_runs_it_and_returns_where_to_look(env):
    client, db = env
    p = make_project(db)
    pages = {f"https://rival{i}.com/g": _page(["Eligibility criteria", "Fees"]) for i in range(1, 4)}
    live = _page(["Overview"])
    with patch.object(cg.serp_evidence, "fetch_serp_evidence", return_value=_serp()), \
         patch.object(cg.page_evidence, "fetch_page_evidence", return_value=live), \
         patch.object(cg.page_evidence, "fetch_pages_evidence", side_effect=lambda urls, *a, **k: {u: pages[u] for u in urls}), \
         patch.object(cg.ai_provider, "generate_action_plan", return_value={"status": "no_data", "actions": [], "rejected": [], "warnings": []}):
        r = client.post(f"/projects/{p.id}/competitors/gap-analysis", data=FORM)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["url"] == f"/projects/{p.id}/competitors?run={body['run_id']}"
    assert client.get(body["url"]).status_code == 200


def test_a_provider_failure_is_a_200_with_the_failure_recorded_not_a_500(env):
    client, db = env
    p = make_project(db)
    with patch.object(cg.page_evidence, "fetch_page_evidence", return_value=_page(["Overview"])), \
         patch.object(cg.serp_evidence, "fetch_serp_evidence", return_value=se.normalize_serp({"error": "dataforseo: 402"})):
        r = client.post(f"/projects/{p.id}/competitors/gap-analysis", data=FORM)
    assert r.status_code == 200 and r.json()["status"] == "error" and "402" in r.json()["error"]
    assert "This analysis failed." in card(client.get(r.json()["url"]).text)


@pytest.mark.parametrize("override,fragment", [
    ({"target_url": "https://other.com/x"}, "must be on this project"),
    ({"target_url": ""}, "full page URL"),
    ({"keyword": "x"}, "target keyword"),
    ({"location": "ZZ"}, "Unsupported location"),
    ({"device": "tablet"}, "desktop or mobile"),
])
def test_bad_input_is_a_400_with_a_readable_reason_and_calls_nothing(env, override, fragment):
    client, db = env
    p = make_project(db)
    with patch.object(cg.serp_evidence, "fetch_serp_evidence") as s:
        r = client.post(f"/projects/{p.id}/competitors/gap-analysis", data={**FORM, **override})
    assert r.status_code == 400 and fragment in r.json()["detail"]
    s.assert_not_called()
    assert db.query(models.CompetitorAnalysisRun).count() == 0


def test_a_second_analysis_while_one_runs_is_a_409(env):
    client, db = env
    p = make_project(db)
    cg._running.add(p.id)
    r = client.post(f"/projects/{p.id}/competitors/gap-analysis", data=FORM)
    assert r.status_code == 409 and "already running" in r.json()["detail"]


# ── drafts ────────────────────────────────────────────────────────────────

DRAFT = {"draft": "Who can apply? Candidates need a bachelor's degree.", "claims_to_verify": [], "warnings": []}


def test_generate_then_decide_over_http(env):
    client, db = env
    p = make_project(db)
    run = seed_run(db, p)
    base = f"/projects/{p.id}/competitors/gap-analysis/{run.id}/actions/action_001"
    with patch.object(cg.ai_provider, "generate_gap_draft", return_value=DRAFT):
        r = client.post(base + "/draft")
    assert r.status_code == 200 and r.json()["draft"]["status"] == "pending"
    r = client.post(base + "/decision", data={"decision": "edit", "text": "My version."})
    assert r.status_code == 200 and r.json()["draft"]["status"] == "edited" and r.json()["draft"]["edited_text"] == "My version."
    html = card(client.get(f"/projects/{p.id}/competitors?run={run.id}").text)
    assert "My version." in html and "edited by you" in html


@pytest.mark.parametrize("action_id,action_type,exc,status", [
    ("action_999", "add", None, 404),
    ("action_001", "restructure", None, 400),
    ("action_001", "add", AIGenerationError("no usable draft"), 502),
])
def test_draft_error_statuses(env, action_id, action_type, exc, status):
    client, db = env
    p = make_project(db)
    run = seed_run(db, p, actions=[{**ACTION, "type": action_type}])
    with patch.object(cg.ai_provider, "generate_gap_draft", side_effect=exc, return_value=DRAFT):
        r = client.post(f"/projects/{p.id}/competitors/gap-analysis/{run.id}/actions/{action_id}/draft")
    assert r.status_code == status and isinstance(r.json()["detail"], str)


def test_a_draft_already_being_generated_is_a_409(env):
    client, db = env
    p = make_project(db)
    run = seed_run(db, p)
    cg._drafting.add((run.id, "action_001"))
    with patch.object(cg.ai_provider, "generate_gap_draft") as g:
        r = client.post(f"/projects/{p.id}/competitors/gap-analysis/{run.id}/actions/action_001/draft")
    assert r.status_code == 409
    g.assert_not_called()


@pytest.mark.parametrize("data,status", [
    ({"decision": "accept"}, 400),                        # no draft yet
    ({"decision": "publish"}, 400),                       # not a decision
    ({}, 400),
])
def test_bad_decisions_are_400(env, data, status):
    client, db = env
    p = make_project(db)
    run = seed_run(db, p)
    r = client.post(f"/projects/{p.id}/competitors/gap-analysis/{run.id}/actions/action_001/decision", data=data)
    assert r.status_code == status


def test_deciding_on_an_unknown_action_is_404(env):
    client, db = env
    p = make_project(db)
    run = seed_run(db, p)
    r = client.post(f"/projects/{p.id}/competitors/gap-analysis/{run.id}/actions/action_404/decision", data={"decision": "accept"})
    assert r.status_code == 404


def test_the_detail_endpoint_returns_the_read_model(env):
    client, db = env
    p = make_project(db)
    run = seed_run(db, p)
    d = client.get(f"/projects/{p.id}/competitors/gap-analysis/{run.id}").json()
    assert d["id"] == run.id and d["keyword"] == "b ed admission" and len(d["snapshots"]) == 3
    assert d["plan"]["actions"][0]["draftable"] is True and d["gaps"][0]["label"] == "Eligibility criteria"


def test_the_start_endpoint_can_gather_evidence_only_and_the_page_says_so(env):
    client, db = env
    p = make_project(db)
    pages = {f"https://rival{i}.com/g": _page(["Eligibility criteria", "Fees"]) for i in range(1, 4)}
    with patch.object(cg.serp_evidence, "fetch_serp_evidence", return_value=_serp()), \
         patch.object(cg.page_evidence, "fetch_page_evidence", return_value=_page(["Overview"])), \
         patch.object(cg.page_evidence, "fetch_pages_evidence", side_effect=lambda urls, *a, **k: {u: pages[u] for u in urls}), \
         patch.object(cg.ai_provider, "generate_action_plan") as ai:
        r = client.post(f"/projects/{p.id}/competitors/gap-analysis", data={**FORM, "with_plan": "false"})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    ai.assert_not_called()
    html = card(client.get(r.json()["url"]).text)
    assert "An action plan was not requested for this analysis" in html and "Draft this" not in html
