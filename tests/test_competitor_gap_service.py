"""
The competitor-gap orchestrator: every stage's outcome is recorded, a failure is never
rendered as an empty success, and nothing is published. In-memory SQLite; the SERP,
page fetching and the AI are all mocked (no network, no spend).
"""
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.ai_errors import AIGenerationError
from app.services import competitor_gap as cg
from app.services import serp_evidence as se


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


@pytest.fixture(autouse=True)
def _no_leftover_running_flags():
    cg._running.clear()
    yield
    cg._running.clear()


TARGET = "https://mine.com/b-ed-admission"
KW = "b ed admission"


def make_project(db, with_page=True, page_extra=None):
    project = models.Project(name="P", base_url="https://mine.com")
    db.add(project)
    db.flush()
    page = None
    if with_page:
        fields = dict(
            project_id=project.id, url=TARGET, title="B.Ed admission", h1=["B.Ed admission"],
            heading_structure=[{"tag": "h2", "text": "Overview"}, {"tag": "h3", "text": "Contact"}],
            custom_content="This page gives an overview of the B.Ed course and how to contact us. " * 5, word_count=120,
        )
        fields.update(page_extra or {})
        page = models.Page(**fields)
        db.add(page)
    db.commit()
    return project, page


def serp_result(urls, source="dataforseo", paa=None, features=None):
    items = [{"type": "organic", "rank_group": i, "url": u, "title": f"Title {i}", "description": "d"} for i, u in enumerate(urls, start=1)]
    if paa:
        items.append({"type": "people_also_ask", "items": [{"title": q} for q in paa]})
    return se.normalize_serp({"items": items, "features": features or {}, "_source": source})


def page_result(headings=(), words=400, questions=(), status="ok", error=None, method="http", h1="Guide"):
    return {
        "status": status, "fetch_method": method, "title": h1, "h1": h1,
        "headings": [{"tag": "h2", "text": h} for h in headings],
        "text": ("body text about the admission process. " * (words // 6)) if status == "ok" else "",
        "word_count": words if status == "ok" else 0, "questions": list(questions),
        "extraction_confidence": "high" if status == "ok" else "low", "error": error,
    }


COMP_URLS = [f"https://rival{i}.com/guide" for i in range(1, 6)]
HEADINGS = ["Eligibility criteria", "Documents required", "Age limit"]


def full_results(urls=COMP_URLS):
    """5 competitors; 'Eligibility criteria' on 4, 'Documents required' on 3.
    Given fewer urls, the first N of those pages are returned."""
    pages = [
        page_result(["Eligibility criteria", "Documents required", "Fees"]),
        page_result(["Eligibility criteria", "Documents required"]),
        page_result(["Eligibility criteria", "Documents required", "Syllabus"]),
        page_result(["Eligibility criteria", "Selection process"]),
        page_result(["Fees", "Scholarships"]),
    ]
    return dict(zip(urls, pages))


OK_PLAN = {"status": "ok", "actions": [{
    "id": "action_001", "type": "add", "priority": "high", "title": "Add eligibility", "problem": "missing",
    "recommendation": "Add a section on who can apply.", "confidence": "high", "requires_fact_check": False,
    "evidence": [{"type": "topic_consensus", "label": "Eligibility criteria", "competitor_count": 4, "competitor_total": 5, "gap_id": None}],
}], "rejected": [], "warnings": []}


def run(db, project, serp=None, results=None, plan=None, plan_effect=None, target=TARGET, keyword=KW, location="in", device="desktop"):
    """plan_effect: an exception to raise, or a function(db, bundle, profile) -> plan, in place of the fixed plan."""
    serp = serp if serp is not None else serp_result(COMP_URLS)
    results = results if results is not None else full_results()
    with patch.object(cg.serp_evidence, "fetch_serp_evidence", return_value=serp) as s, \
         patch.object(cg.page_evidence, "fetch_pages_evidence", side_effect=lambda urls, *a, **k: {u: results[u] for u in urls}) as f, \
         patch.object(cg.ai_provider, "generate_action_plan", return_value=plan or OK_PLAN, side_effect=plan_effect) as ai:
        out = cg.run_analysis(db, project, target, keyword, location, device)
    return out, s, f, ai


# ── input validation ──────────────────────────────────────────────────────

@pytest.mark.parametrize("url", ["", "mine.com/page", "ftp://mine.com/x", "https://other.com/page", "https://mine.com.evil.com/x", "javascript:alert(1)", "https://" + "a" * 2100 + ".mine.com"])
def test_bad_or_foreign_target_urls_are_refused(db, url):
    project, _ = make_project(db)
    with pytest.raises(cg.InputError):
        cg.validate_inputs(project, url, KW, "IN", "desktop")


def test_a_subdomain_of_the_projects_site_is_fine_and_inputs_are_normalised(db):
    project, _ = make_project(db)
    out = cg.validate_inputs(project, "https://blog.mine.com/x", "  b   ed  admission ", "in", "MOBILE")
    assert out == {"target_url": "https://blog.mine.com/x", "keyword": "b ed admission", "location": "IN", "device": "mobile"}


@pytest.mark.parametrize("kw,loc,dev", [("a", "IN", "desktop"), ("", "IN", "desktop"), ("x" * 201, "IN", "desktop"),
                                        (KW, "ZZ", "desktop"), (KW, "", "desktop"), (KW, "IN", "tablet")])
def test_bad_keyword_location_or_device_are_refused(db, kw, loc, dev):
    project, _ = make_project(db)
    with pytest.raises(cg.InputError):
        cg.validate_inputs(project, TARGET, kw, loc, dev)


def test_invalid_input_creates_no_run_and_calls_no_provider(db):
    project, _ = make_project(db)
    with patch.object(cg.serp_evidence, "fetch_serp_evidence") as s:
        with pytest.raises(cg.InputError):
            cg.run_analysis(db, project, "https://other.com/x", KW, "IN", "desktop")
    s.assert_not_called()
    assert db.query(models.CompetitorAnalysisRun).count() == 0


# ── the happy path ────────────────────────────────────────────────────────

def test_a_full_run_records_every_stage(db):
    project, page = make_project(db)
    out, s, f, ai = run(db, project)
    assert out.status == "ok" and out.error is None and out.plan_status == "ok" and out.page_id == page.id
    assert (out.competitors_selected, out.competitors_analyzed, out.serp_total_results) == (5, 5, 5)
    assert out.location == "IN" and out.device == "desktop" and out.source == "dataforseo"
    assert out.intent and "label" in out.intent and out.format_distribution
    s.assert_called_once_with(KW, "IN", "desktop")
    assert cg.summary_line(out) == "5 of 5 comparable competitors successfully analysed"

    gaps = db.query(models.CompetitorGap).filter_by(analysis_run_id=out.id).all()
    elig = next(g for g in gaps if "eligibility" in g.label.lower())
    assert (elig.competitor_count, elig.competitor_total, elig.target_coverage, elig.gap_type) == (4, 5, "missing", "topic")
    assert len(db.query(models.CompetitorPageSnapshot).filter_by(analysis_run_id=out.id).all()) == 5


def test_the_target_comes_from_crawled_data_without_a_live_fetch(db):
    project, _ = make_project(db)
    with patch.object(cg.page_evidence, "fetch_page_evidence") as live:
        out, *_ = run(db, project)
    live.assert_not_called()
    assert out.target_snapshot["source"] == "the project's crawled page data" and out.target_snapshot["title"] == "B.Ed admission"


def test_excluded_results_are_recorded_with_the_reason_and_never_fetched(db):
    project, _ = make_project(db)
    urls = ["https://www.reddit.com/r/x/comments/1", "https://mine.com/other", "https://rival1.com/guide", "https://rival2.com/guide", "https://rival3.com/guide"]
    results = {u: page_result(["Eligibility criteria"]) for u in urls[2:]}
    out, _, f, _ = run(db, project, serp=serp_result(urls), results=results)
    snaps = {s.url: s for s in db.query(models.CompetitorPageSnapshot).filter_by(analysis_run_id=out.id)}
    assert snaps[urls[0]].fetch_status == "skipped" and "forum" in snaps[urls[0]].error and snaps[urls[0]].selected is False
    assert "own domain" in snaps[urls[1]].error
    assert sorted(f.call_args.args[0]) == sorted(urls[2:])  # only the comparable ones were fetched
    assert out.competitors_selected == 3


def _topic_row(db, run_id, label_part):
    return (db.query(models.CompetitorGap)
            .filter(models.CompetitorGap.analysis_run_id == run_id, models.CompetitorGap.gap_type == "topic",
                    models.CompetitorGap.label.ilike(f"%{label_part}%")).one())


def _plan_citing(label_part, action_type):
    """A fake AI that cites the real evidence item for the topic whose label contains label_part."""
    def make(_db, bundle, _profile):
        ev = next(e for e in bundle["evidence"] if label_part in e["label"].lower())
        cited = {"type": ev["type"], "label": ev["label"], "competitor_count": ev["competitor_count"],
                 "competitor_total": ev["competitor_total"], "gap_id": ev["gap_id"]}
        return {"status": "ok", "rejected": [], "warnings": [],
                "actions": [{**OK_PLAN["actions"][0], "type": action_type, "evidence": [cited]}]}
    return make


def test_recommended_actions_are_filled_from_the_validated_plan(db):
    project, _ = make_project(db)
    baseline, *_ = run(db, project)
    assert _topic_row(db, baseline.id, "eligibility").recommended_action == "add"   # the default for a missing topic

    refined, *_ = run(db, project, plan_effect=_plan_citing("eligibility", "rewrite"))
    assert _topic_row(db, refined.id, "eligibility").recommended_action == "rewrite"
    # a gap the plan did not cite keeps its deterministic default
    assert _topic_row(db, refined.id, "documents").recommended_action == _topic_row(db, baseline.id, "documents").recommended_action


def test_the_ai_receives_numbered_evidence_and_no_raw_competitor_text(db):
    project, _ = make_project(db)
    _, _, _, ai = run(db, project)
    bundle = ai.call_args.args[1]
    assert bundle["evidence"][0]["id"] == "E01" and all(e["gap_id"] for e in bundle["evidence"])
    flat = repr(bundle)
    assert "body text about the admission process" not in flat            # competitor body text is never sent
    assert bundle["serp"]["analyzed"] == 5 and bundle["serp"]["selected"] == 5 and bundle["keyword"] == KW


# ── the target page ───────────────────────────────────────────────────────

def test_a_page_not_in_the_project_is_fetched_live(db):
    project, _ = make_project(db, with_page=False)
    live = {**page_result(["Overview"], h1="Mine"), "title": "Mine"}
    with patch.object(cg.page_evidence, "fetch_page_evidence", return_value=live) as lf:
        out, *_ = run(db, project)
    lf.assert_called_once_with(TARGET)
    assert out.status == "ok" and out.page_id is None and "live fetch" in out.target_snapshot["source"]


def test_a_crawled_page_with_no_usable_data_is_fetched_live(db):
    project, _ = make_project(db, with_page=False)
    db.add(models.Page(project_id=project.id, url=TARGET))
    db.commit()
    with patch.object(cg.page_evidence, "fetch_page_evidence", return_value=page_result(["Overview"])) as lf:
        out, *_ = run(db, project)
    lf.assert_called_once()
    assert out.status == "ok"


def test_an_unreadable_target_is_an_error_and_the_serp_is_never_paid_for(db):
    project, _ = make_project(db, with_page=False)
    bad = page_result(status="error", error="blocked (HTTP 403)")
    with patch.object(cg.page_evidence, "fetch_page_evidence", return_value=bad):
        out, s, f, ai = run(db, project)
    assert out.status == "error" and "could not be read" in out.error and "403" in out.error
    s.assert_not_called(); f.assert_not_called(); ai.assert_not_called()


# ── failures are visible, never an empty success ──────────────────────────

def test_a_serp_error_is_an_error_with_the_reason(db):
    project, _ = make_project(db)
    out, _, f, ai = run(db, project, serp=se.normalize_serp({"error": "dataforseo: 402 payment required; semrush: no key"}))
    assert out.status == "error" and "402" in out.error and out.plan_status == "not_run"
    assert db.query(models.CompetitorGap).count() == 0
    f.assert_not_called(); ai.assert_not_called()


def test_a_serp_with_no_organic_results_is_no_data(db):
    project, _ = make_project(db)
    out, *_ = run(db, project, serp=se.normalize_serp({"items": [], "_source": "dataforseo"}))
    assert out.status == "no_data" and out.error


def test_only_non_comparable_results_is_no_data_and_they_are_still_recorded(db):
    project, _ = make_project(db)
    urls = ["https://www.reddit.com/r/x/comments/1", "https://www.amazon.in/dp/1", "https://home.com/"]
    out, _, f, _ = run(db, project, serp=serp_result(urls), results={})
    assert out.status == "no_data" and "comparable" in out.error
    f.assert_not_called()
    assert db.query(models.CompetitorPageSnapshot).filter_by(analysis_run_id=out.id).count() == 3


def test_partial_fetches_are_partial_and_the_failures_keep_their_error(db):
    project, _ = make_project(db)
    results = full_results()
    results[COMP_URLS[3]] = page_result(status="error", error="blocked (HTTP 403: login, paywall or access control)")
    results[COMP_URLS[4]] = page_result(status="no_data", error="the page had no extractable text")
    out, *_ = run(db, project, results=results)
    assert out.status == "partial" and (out.competitors_selected, out.competitors_analyzed) == (5, 3)
    assert cg.summary_line(out) == "3 of 5 comparable competitors successfully analysed"
    snaps = {s.url: s for s in db.query(models.CompetitorPageSnapshot).filter_by(analysis_run_id=out.id)}
    assert snaps[COMP_URLS[3]].fetch_status == "error" and "403" in snaps[COMP_URLS[3]].error
    assert snaps[COMP_URLS[4]].fetch_status == "no_data" and snaps[COMP_URLS[3]].text in (None, "")
    gaps = db.query(models.CompetitorGap).filter_by(analysis_run_id=out.id).all()
    assert gaps and all(g.competitor_total == 3 for g in gaps if g.gap_type in ("topic", "question", "query"))  # counted over the pages that worked
    assert any("3 of 5" in n for n in cg.notices(out))


def test_fewer_than_two_analysable_pages_means_no_pattern_not_a_fake_result(db):
    project, _ = make_project(db)
    results = {u: page_result(status="error", error="timed out") for u in COMP_URLS}
    results[COMP_URLS[0]] = page_result(["Eligibility criteria"])
    out, _, _, ai = run(db, project, results=results)
    assert out.status == "no_data" and "Only 1 of 5" in out.error
    assert db.query(models.CompetitorGap).count() == 0
    ai.assert_not_called()


def test_an_ai_failure_keeps_the_gaps_and_is_reported_as_the_plans_failure(db):
    project, _ = make_project(db)
    out, *_ = run(db, project, plan_effect=AIGenerationError("AI provider call failed: 529"))
    assert out.status == "ok"                                   # the evidence itself is fine
    assert out.plan_status == "error" and "529" in out.plan_error and out.action_plan_json is None
    assert db.query(models.CompetitorGap).filter_by(analysis_run_id=out.id).count() > 0


def test_an_ai_answer_that_is_entirely_rejected_is_no_data(db):
    project, _ = make_project(db)
    plan = {"status": "no_data", "actions": [], "rejected": [{"id": "a1", "reason": "cites no evidence"}], "warnings": []}
    out, *_ = run(db, project, plan=plan)
    assert out.plan_status == "no_data" and "rejected" in out.plan_error and out.action_plan_json["rejected"]


def _fake_analysis(coverages):
    """What gap_analysis.analyse returns, with the given coverage for each of its topic gaps."""
    return {
        "gaps": [{"gap_type": "topic", "label": f"Topic {i}", "target_coverage": cov, "competitor_count": 4, "competitor_total": 5,
                  "evidence": {}, "confidence": "high", "recommended_action": "leave_unchanged" if cov == "covered" else "add"}
                 for i, cov in enumerate(coverages, start=1)],
        "intent": {"label": "informational"}, "format_distribution": {"guide": 5}, "question_data_available": True,
    }


def test_when_the_page_already_covers_everything_the_ai_is_not_called(db):
    project, _ = make_project(db)
    with patch.object(cg.gap_analysis, "analyse", return_value=_fake_analysis(["covered", "covered"])):
        out, _, _, ai = run(db, project)
    ai.assert_not_called()
    assert out.status == "ok" and out.plan_status == "no_data" and "No gaps found" in out.plan_error
    assert db.query(models.CompetitorGap).filter_by(analysis_run_id=out.id).count() == 2   # the coverage is still shown


def test_one_uncovered_gap_is_enough_to_ask_the_ai(db):
    project, _ = make_project(db)
    with patch.object(cg.gap_analysis, "analyse", return_value=_fake_analysis(["covered", "partial"])):
        out, _, _, ai = run(db, project)
    ai.assert_called_once()
    assert out.plan_status == "ok"


def test_semrush_fallback_is_disclosed_and_passed_to_the_ai(db):
    project, _ = make_project(db)
    out, _, _, ai = run(db, project, serp=serp_result(COMP_URLS, source="semrush"))
    assert any("Semrush" in n and "People-Also-Ask" in n for n in cg.notices(out))
    assert ai.call_args.args[1]["serp"]["question_data_available"] is False


def test_a_small_sample_is_flagged_low_confidence(db):
    project, _ = make_project(db)
    urls = COMP_URLS[:3]
    out, *_ = run(db, project, serp=serp_result(urls), results={u: full_results(urls)[u] for u in urls})
    assert any("Small sample" in n for n in cg.notices(out))


# ── crashes and concurrency ───────────────────────────────────────────────

def test_a_crash_mid_run_leaves_a_visible_error_row_and_frees_the_project(db):
    project, _ = make_project(db)
    with patch.object(cg.gap_analysis, "analyse", side_effect=RuntimeError("kaboom")):
        out, *_ = run(db, project)
    assert out.status == "error" and "kaboom" in out.error
    assert project.id not in cg._running
    assert db.query(models.CompetitorAnalysisRun).count() == 1


def test_a_second_analysis_while_one_is_running_is_refused_then_allowed_afterwards(db):
    project, _ = make_project(db)
    cg._running.add(project.id)
    with pytest.raises(cg.BusyError):
        cg.run_analysis(db, project, TARGET, KW, "IN", "desktop")
    assert db.query(models.CompetitorAnalysisRun).count() == 0
    cg._running.discard(project.id)
    out, *_ = run(db, project)
    assert out.status == "ok" and project.id not in cg._running


def test_history_is_kept_each_run_is_its_own_row(db):
    project, _ = make_project(db)
    run(db, project); run(db, project)
    assert db.query(models.CompetitorAnalysisRun).count() == 2


# ── read model ────────────────────────────────────────────────────────────

def test_run_detail_shape(db):
    project, _ = make_project(db)
    out, *_ = run(db, project)
    d = cg.run_detail(db, out)
    assert d["summary_line"].startswith("5 of 5") and d["status"] == "ok" and d["keyword"] == KW
    assert [s["position"] for s in d["snapshots"]] == [1, 2, 3, 4, 5]
    assert all({"fetch_status", "fetch_method", "error", "result_class", "selected"} <= set(s) for s in d["snapshots"])
    assert d["gaps"] and all({"label", "target_coverage", "competitor_count", "competitor_total", "evidence"} <= set(g) for g in d["gaps"])
    assert d["plan"]["status"] == "ok" and d["plan"]["actions"][0]["draftable"] is True


def test_no_score_field_exists_anywhere_in_the_output(db):
    project, _ = make_project(db)
    out, *_ = run(db, project)
    assert "score" not in repr(cg.run_detail(db, out)).lower()


# ── drafts ────────────────────────────────────────────────────────────────

def make_run_with_plan(db, action_type="add"):
    project, _ = make_project(db)
    out, *_ = run(db, project)
    plan = {"actions": [{**OK_PLAN["actions"][0], "type": action_type}], "rejected": [], "warnings": []}
    out.action_plan_json = plan
    db.commit()
    return out


DRAFT = {"draft": "Who can apply? Candidates need a bachelor's degree from a recognised university.", "claims_to_verify": [], "warnings": []}


def test_generating_a_draft_saves_it_as_pending_next_to_its_action(db):
    r = make_run_with_plan(db)
    with patch.object(cg.ai_provider, "generate_gap_draft", return_value=DRAFT) as g:
        draft = cg.generate_draft(db, r, "action_001")
    assert draft["status"] == "pending" and draft["text"].startswith("Who can apply?")
    texts = g.call_args.args[3]
    assert texts and all("body text about the admission process" in t for t in texts)  # competitor texts are for the COPY CHECK only
    db.expire_all()
    assert db.get(models.CompetitorAnalysisRun, r.id).action_plan_json["actions"][0]["draft"]["status"] == "pending"


def test_only_text_producing_actions_can_be_drafted(db):
    for t in ("restructure", "separate_page", "leave_unchanged"):
        r = make_run_with_plan(db, t)
        with patch.object(cg.ai_provider, "generate_gap_draft") as g:
            with pytest.raises(cg.InputError):
                cg.generate_draft(db, r, "action_001")
        g.assert_not_called()


def test_an_unknown_action_is_not_found(db):
    r = make_run_with_plan(db)
    with pytest.raises(LookupError):
        cg.generate_draft(db, r, "action_999")


def test_a_failed_generation_saves_nothing(db):
    r = make_run_with_plan(db)
    with patch.object(cg.ai_provider, "generate_gap_draft", side_effect=AIGenerationError("no good draft")):
        with pytest.raises(AIGenerationError):
            cg.generate_draft(db, r, "action_001")
    db.expire_all()
    assert "draft" not in db.get(models.CompetitorAnalysisRun, r.id).action_plan_json["actions"][0]


@pytest.mark.parametrize("decision,status", [("accept", "accepted"), ("reject", "rejected")])
def test_accept_and_reject_are_recorded(db, decision, status):
    r = make_run_with_plan(db)
    with patch.object(cg.ai_provider, "generate_gap_draft", return_value=DRAFT):
        cg.generate_draft(db, r, "action_001")
    d = cg.decide_draft(db, r, "action_001", decision)
    assert d["status"] == status and d["decided_at"]
    db.expire_all()
    assert db.get(models.CompetitorAnalysisRun, r.id).action_plan_json["actions"][0]["draft"]["status"] == status


def test_edit_keeps_both_the_original_and_the_edit(db):
    r = make_run_with_plan(db)
    with patch.object(cg.ai_provider, "generate_gap_draft", return_value=DRAFT):
        cg.generate_draft(db, r, "action_001")
    d = cg.decide_draft(db, r, "action_001", "edit", "My own version of the text.")
    assert d["status"] == "edited" and d["edited_text"] == "My own version of the text." and d["text"].startswith("Who can apply?")


@pytest.mark.parametrize("decision,text", [("edit", ""), ("edit", "   "), ("edit", "x" * 4000), ("publish", None), ("", None)])
def test_bad_decisions_are_refused(db, decision, text):
    r = make_run_with_plan(db)
    with patch.object(cg.ai_provider, "generate_gap_draft", return_value=DRAFT):
        cg.generate_draft(db, r, "action_001")
    with pytest.raises(cg.InputError):
        cg.decide_draft(db, r, "action_001", decision, text)


def test_deciding_without_a_draft_is_refused(db):
    r = make_run_with_plan(db)
    with pytest.raises(cg.InputError):
        cg.decide_draft(db, r, "action_001", "accept")


def test_an_accepted_or_edited_draft_is_never_regenerated_but_a_rejected_one_can_be(db):
    r = make_run_with_plan(db)
    with patch.object(cg.ai_provider, "generate_gap_draft", return_value=DRAFT) as g:
        cg.generate_draft(db, r, "action_001")
        cg.decide_draft(db, r, "action_001", "accept")
        with pytest.raises(cg.InputError):
            cg.generate_draft(db, r, "action_001")
        assert g.call_count == 1
        cg.decide_draft(db, r, "action_001", "reject")
        cg.generate_draft(db, r, "action_001")   # rejected: a fresh attempt is allowed
        assert g.call_count == 2


def test_nothing_in_the_service_can_publish(db):
    """Behaviour: generating and accepting a draft changes nothing but the run's own JSON.
    Structure: the module does not import WordPress or any deploy code at all."""
    import ast
    import inspect

    r = make_run_with_plan(db)
    page_before = db.query(models.Page).one().custom_content
    with patch.object(cg.ai_provider, "generate_gap_draft", return_value=DRAFT):
        cg.generate_draft(db, r, "action_001")
        cg.decide_draft(db, r, "action_001", "accept")
    assert db.query(models.Suggestion).count() == 0 and db.query(models.Issue).count() == 0
    assert db.query(models.Page).one().custom_content == page_before

    imported = []
    for node in ast.walk(ast.parse(inspect.getsource(cg))):
        if isinstance(node, ast.ImportFrom):
            imported += [f"{node.module or ''}.{a.name}" for a in node.names]
        elif isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
    assert imported, "the import scan found nothing, so it proves nothing"
    assert not [i for i in imported if "wordpress" in i.lower() or "deploy" in i.lower() or "requests" in i.lower()], imported
