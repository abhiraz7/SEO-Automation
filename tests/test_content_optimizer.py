"""
The optimizer orchestrator against a real (in-memory) database: evidence reuse, every
run outcome, what is stored through the existing Suggestion workflow, validation
results stored as data, and regeneration. Only the AI and the search-evidence gathering
are mocked; nothing touches the network.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models, schemas
from app.ai_errors import AIGenerationError
from app.services import content_optimizer as co

URL = "https://mine.com/b-ed-admission"
KW = "b ed admission"
SENTENCE = ("Applicants need a bachelor's degree from a recognised university and must submit two recent photographs "
            "with the application form before the closing date.")
MD = f"# Teacher training\n\n## Eligibility criteria\n\n{SENTENCE}\n\n## Fees\n\nThe fees vary by college.\n"


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
def _clean_guards():
    co._running.clear()
    yield
    co._running.clear()


def make_world(db, **page_over):
    project = models.Project(name="P", base_url="https://mine.com")
    db.add(project)
    db.flush()
    fields = dict(project_id=project.id, url=URL, source="crawler", title="Teacher training course", meta_description="A course for teachers.",
                  h1=["Teacher training"], h2=[], lang="en", markdown=MD, word_count=120)
    fields.update(page_over)
    page = models.Page(**fields)
    db.add(page)
    db.commit()
    return project, page


def make_evidence(db, project, page, status="ok", keyword=KW, location="IN", device="desktop", age_hours=0, source="dataforseo", error=None, with_gaps=True):
    run = models.CompetitorAnalysisRun(
        project_id=project.id, page_id=page.id, target_url=page.url, keyword=keyword, location=location, device=device, status=status, error=error,
        source=source, serp_features=["people_also_ask"], serp_total_results=10, intent={"label": "informational", "confidence": "medium", "signals": []},
        format_distribution={"guide": 5}, competitors_selected=5, competitors_analyzed=5, plan_status="not_run",
        created_at=datetime.now(timezone.utc) - timedelta(hours=age_hours),
    )
    db.add(run)
    db.flush()
    if with_gaps:
        for gtype, label, n, cov in [("topic", "Documents required", 4, "missing"), ("question", "Who can apply for B.Ed?", 3, "missing")]:
            db.add(models.CompetitorGap(analysis_run_id=run.id, gap_type=gtype, label=label, target_coverage=cov, competitor_count=n, competitor_total=5,
                                        evidence_json={}, confidence="high", recommended_action="add"))
        db.add(models.CompetitorPageSnapshot(analysis_run_id=run.id, url="https://rival1.com/guide", position=1, result_class="direct_content", selected=True,
                                             title="Rival guide to B.Ed admission", h1="Rival guide", headings_json=[{"tag": "h2", "text": "Documents you need"}],
                                             text="Completely unrelated competitor text about campus sports facilities and hostel rooms for every student",
                                             word_count=300, fetch_method="http", fetch_status="ok"))
    db.commit()
    return run


def model_item(type_="add_section", target="new", label="Documents required",
               after="## Documents required\n\nBring your degree certificate, recent photographs and a photo identity card to the counter.",
               problem="The page never lists the documents required.", **kw):
    """A model answer item whose evidence is chosen by LABEL once the bundle exists."""
    def build(bundle):
        ids = [e["id"] for e in bundle["evidence"] if label in e["label"]]
        base = {"type": type_, "target": target, "priority": "high", "problem": problem, "evidence_ids": ids, "after": after,
                "requires_fact_check": False, "claims_to_verify": [], "confidence": "high"}
        base.update(kw)
        return base
    return build


def ai_says(*items, no_change_reason=None, malformed=()):
    def respond(db, bundle, profile):
        built = [schemas.ModelOptimizerSuggestion.model_validate(i(bundle) if callable(i) else i) for i in items]
        return {"items": built, "malformed": list(malformed), "no_change_reason": no_change_reason}
    return respond


def optimize(db, project, page, ai, evidence_run_id=None, refresh=False, keyword=KW):
    with patch.object(co.ai_provider, "generate_optimizer_suggestions", side_effect=ai) as m:
        run = co.run_optimization(db, project, page.id, keyword, "IN", "desktop", evidence_run_id, refresh)
    return run, m


def rows(db):
    return (db.query(models.Suggestion, models.SuggestionOptimization, models.Issue)
            .join(models.SuggestionOptimization, models.SuggestionOptimization.suggestion_id == models.Suggestion.id)
            .join(models.Issue, models.Issue.id == models.Suggestion.issue_id).order_by(models.Suggestion.id).all())


def check(meta, name):
    return next(c for c in meta.validation_json["checks"] if c["name"] == name)


# ── a normal run, stored through the existing workflow ────────────────────

def test_a_valid_run_stores_ordinary_suggestions_under_protected_issues(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    run, m = optimize(db, project, page, ai_says(
        model_item(),
        model_item("improve_title", "title", label="title does not contain", after="B.Ed admission: the complete guide for teachers", problem="The title omits the keyword.")))
    assert run.status == "ok" and run.error is None and run.evidence_reused is True
    m.assert_called_once()
    stored = rows(db)
    assert {(i.category, i.rule, i.severity) for _, _, i in stored} == {("content", "opt_add_section", "info"), ("title", "opt_improve_title", "info")}
    assert all(s.status == "pending" and s.accepted_at is None and s.image_src is None for s, _, _ in stored)
    assert all(models.is_optimizer_rule(i.rule) for _, _, i in stored)
    section = next(x for x in stored if x[1].suggestion_type == "add_section")
    assert section[0].content.startswith("## Documents required") and section[1].before_content is None and section[1].target_label == "New section"
    assert section[0].content_hash == co.content_hash(section[0].content)


def test_before_is_the_pages_real_text_and_the_diff_shows_the_change(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    after = "The fees vary by college and by course, and are published before each admission round."
    run, _ = optimize(db, project, page, ai_says(model_item("expand_section", "sec_02", label="Documents required", after=after, problem="The fees section is thin.")))
    (s, meta, _), = rows(db)
    assert meta.before_content == "The fees vary by college." and meta.target_label == "Section: Fees"
    view = co.run_detail(db, run)["suggestions"][0]
    assert view["before"] == "The fees vary by college." and "<ins" in str(view["diff_html"]) and "<del" not in str(view["diff_html"])


def test_the_model_cannot_change_the_before_text(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    optimize(db, project, page, ai_says(model_item("improve_title", "title", label="title does not contain", after="B.Ed admission: the complete guide", before="FAKE BEFORE")))
    assert rows(db)[0][1].before_content == "Teacher training course"


def test_the_model_is_given_only_evidence_the_application_built(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    _, m = optimize(db, project, page, ai_says(model_item()))
    bundle = m.call_args.args[1]
    ev = {e["label"]: e for e in bundle["evidence"]}
    assert (ev["Documents required"]["competitor_count"], ev["Documents required"]["competitor_total"]) == (4, 5)
    assert bundle["serp"] == {"analyzed": 5, "selected": 5, "total_results": 10, "features": ["people_also_ask"], "question_data_available": True}
    assert "competitor_texts" not in bundle and "Completely unrelated competitor text" not in repr(bundle)
    assert "improve_title" in bundle["allowed_types"] and bundle["allowed_targets"][:3] == ["title", "meta_description", "h1"]


# ── model failures and unusable answers are visible, never an empty result ─

def test_malformed_ai_json_is_an_error_with_the_reason_and_stores_nothing(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    def boom(*a, **k):
        raise AIGenerationError("AI provider returned an unusable answer after retry: the response is not JSON")
    run, _ = optimize(db, project, page, boom)
    assert run.status == "error" and "not JSON" in run.error and rows(db) == []


def test_a_provider_outage_is_an_error_not_no_change(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    def boom(*a, **k):
        raise AIGenerationError("AI provider call failed: 529 overloaded")
    run, _ = optimize(db, project, page, boom)
    assert run.status == "error" and "529" in run.error and run.status != "no_change"


def test_suggestions_that_cite_no_evidence_are_discarded_and_listed_when_all_are_unusable_the_run_is_an_error(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    run, _ = optimize(db, project, page, ai_says(model_item(evidence_ids=["E98"], label="nothing matches this label")))
    assert run.status == "error" and "none could be used" in run.error and "cites no evidence" in run.error
    assert run.notes["discarded"][0]["reason"] == "it cites no evidence from the supplied list" and rows(db) == []


def test_an_unsupported_type_is_discarded_alone_and_the_rest_are_kept(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    run, _ = optimize(db, project, page, ai_says(model_item("rewrite_entire_article"), model_item()))
    assert run.status == "ok" and len(rows(db)) == 1
    assert run.notes["discarded"] == [{"id": "proposal 1", "reason": "unsupported suggestion type 'rewrite_entire_article'"}]


def test_a_malformed_item_inside_a_good_answer_is_reported_and_the_rest_kept(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    run, _ = optimize(db, project, page, ai_says(model_item(), malformed=[{"id": "proposal 2", "reason": "malformed: not an object"}]))
    assert run.status == "ok" and any(d["id"] == "proposal 2" for d in run.notes["discarded"])


def test_no_change_recommended_is_a_real_answer_with_the_models_reason(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    run, _ = optimize(db, project, page, ai_says(no_change_reason="No change recommended. The page already answers the recurring questions."))
    assert run.status == "no_change" and run.error is None and run.notes["no_change_reason"].startswith("No change recommended.") and rows(db) == []
    assert co.run_detail(db, run)["summary_line"] == co.NO_CHANGE


def test_a_page_that_already_covers_everything_needs_no_model_call(db):
    project, page = make_world(db, title="B.Ed admission guide", meta_description="Everything about B.Ed admission.", h1=["B.Ed admission"])
    ev = make_evidence(db, project, page, with_gaps=False)
    db.add(models.CompetitorGap(analysis_run_id=ev.id, gap_type="topic", label="Fees", target_coverage="covered", competitor_count=4, competitor_total=5, evidence_json={}, confidence="high"))
    db.commit()
    run, m = optimize(db, project, page, ai_says(model_item()))
    m.assert_not_called()
    assert run.status == "no_change" and "already covers" in run.notes["no_change_reason"]


# ── evidence: reuse, freshness, and provider failures ─────────────────────

def test_a_fresh_matching_analysis_is_reused_without_gathering(db):
    project, page = make_world(db)
    ev = make_evidence(db, project, page)
    with patch.object(co.competitor_gap, "run_analysis") as gather:
        run, _ = optimize(db, project, page, ai_says(model_item()))
    gather.assert_not_called()
    assert run.evidence_run_id == ev.id and run.evidence_reused is True


@pytest.mark.parametrize("over", [
    {"age_hours": 25}, {"keyword": "other keyword"}, {"location": "US"}, {"device": "mobile"}, {"status": "error", "error": "402"}, {"status": "no_data", "error": "none"},
])
def test_an_analysis_that_is_stale_or_for_something_else_or_failed_is_not_reused(db, over):
    project, page = make_world(db)
    make_evidence(db, project, page, **over)
    fresh = make_evidence(db, project, page, with_gaps=True, keyword="brand new keyword unrelated")
    gathered = {}
    def gather(db_, project_, url, kw, loc, dev, with_plan=True):
        gathered["kw"], gathered["with_plan"] = kw, with_plan
        return make_evidence(db_, project_, page)
    with patch.object(co.competitor_gap, "run_analysis", side_effect=gather):
        run, _ = optimize(db, project, page, ai_says(model_item()))
    assert gathered == {"kw": KW, "with_plan": False} and run.evidence_reused is False and run.evidence_run_id != fresh.id


def test_a_partial_analysis_is_reusable(db):
    project, page = make_world(db)
    ev = make_evidence(db, project, page, status="partial")
    run, _ = optimize(db, project, page, ai_says(model_item()))
    assert run.evidence_run_id == ev.id and run.status == "ok"


def test_refresh_forces_new_evidence_even_when_a_fresh_one_exists(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    with patch.object(co.competitor_gap, "run_analysis", side_effect=lambda *a, **k: make_evidence(db, project, page)) as gather:
        run, _ = optimize(db, project, page, ai_says(model_item()), refresh=True)
    gather.assert_called_once()
    assert run.evidence_reused is False


def test_a_given_analysis_must_belong_to_the_project_and_match_the_request(db):
    project, page = make_world(db)
    other = models.Project(name="Other", base_url="https://other.com")
    db.add(other)
    db.commit()
    foreign = make_evidence(db, other, page)
    with pytest.raises(LookupError):
        optimize(db, project, page, ai_says(model_item()), evidence_run_id=foreign.id)
    mismatch = make_evidence(db, project, page, keyword="a different keyword")
    with pytest.raises(co.InputError):
        optimize(db, project, page, ai_says(model_item()), evidence_run_id=mismatch.id)
    assert db.query(models.ContentOptimizationRun).count() == 0
    good = make_evidence(db, project, page)
    run, _ = optimize(db, project, page, ai_says(model_item()), evidence_run_id=good.id)
    assert run.evidence_run_id == good.id


def test_no_data_evidence_is_no_reliable_recommendation_and_the_ai_is_never_called(db):
    project, page = make_world(db)
    ev = make_evidence(db, project, page, status="no_data", error="Only 1 of 5 comparable pages could be analysed", with_gaps=False)
    run, m = optimize(db, project, page, ai_says(model_item()), evidence_run_id=ev.id)
    m.assert_not_called()
    assert run.status == "no_data" and run.error.startswith(co.NO_RELIABLE) and "Only 1 of 5" in run.error and rows(db) == []


def test_a_search_provider_failure_is_an_error_not_an_empty_serp(db):
    project, page = make_world(db)
    ev = make_evidence(db, project, page, status="error", error="The search results could not be fetched: dataforseo: 402 payment required", with_gaps=False)
    run, m = optimize(db, project, page, ai_says(model_item()), evidence_run_id=ev.id)
    m.assert_not_called()
    assert run.status == "error" and "402 payment required" in run.error and run.status not in ("no_data", "no_change", "ok")


def test_the_semrush_fallback_tells_the_model_question_data_is_unavailable(db):
    project, page = make_world(db)
    make_evidence(db, project, page, source="semrush")
    _, m = optimize(db, project, page, ai_says(model_item()))
    assert m.call_args.args[1]["serp"]["question_data_available"] is False


def test_a_missing_page_or_one_from_another_project_is_not_found_and_bad_input_is_refused(db):
    project, page = make_world(db)
    other = models.Project(name="Other", base_url="https://other.com")
    db.add(other)
    db.commit()
    with pytest.raises(LookupError):
        co.run_optimization(db, other, page.id, KW, "IN", "desktop")
    with pytest.raises(LookupError):
        co.run_optimization(db, project, 9999, KW, "IN", "desktop")
    for kw, loc, dev in [("x", "IN", "desktop"), (KW, "ZZ", "desktop"), (KW, "IN", "tablet")]:
        with pytest.raises(co.InputError):
            co.run_optimization(db, project, page.id, kw, loc, dev)


def test_an_analysis_already_running_is_a_busy_error_and_creates_no_run(db):
    project, page = make_world(db)
    with patch.object(co.competitor_gap, "run_analysis", side_effect=co.BusyError("An analysis is already running for this project.")):
        with pytest.raises(co.BusyError):
            optimize(db, project, page, ai_says(model_item()))
    assert db.query(models.ContentOptimizationRun).count() == 0 and not co._running


def test_a_second_optimization_for_the_same_page_is_refused_while_one_runs(db):
    project, page = make_world(db)
    co._running.add((project.id, page.id))
    with pytest.raises(co.BusyError):
        optimize(db, project, page, ai_says(model_item()))


def test_a_crash_leaves_a_visible_error_row_and_frees_the_page(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    with patch.object(co.optimizer_plan, "build_evidence", side_effect=RuntimeError("kaboom")):
        run, _ = optimize(db, project, page, ai_says(model_item()))
    assert run.status == "error" and "kaboom" in run.error and not co._running


# ── validation results are stored as data, and nothing is dropped ─────────

def stored_meta(db):
    (s, meta, _), = rows(db)
    return s, meta


def test_keyword_over_use_is_stored_as_a_blocked_suggestion_with_the_reason(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    stuffed = "## Documents required\n\n" + " ".join(["b ed admission"] * 7) + " " + " ".join(f"detail{i}" for i in range(40))
    optimize(db, project, page, ai_says(model_item(after=stuffed)))
    s, meta = stored_meta(db)
    assert meta.validation_status == "blocked" and check(meta, "keyword_repetition")["status"] == "blocked"
    assert check(meta, "keyword_repetition")["details"]["normalized_after"] == 7 and s.status == "pending"          # stored and shown, not dropped


def test_moderate_keyword_use_is_a_warning(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    body = "## Documents required\n\n" + " ".join(["b ed admission"] * 3) + " " + " ".join(f"detail{i}" for i in range(40))
    optimize(db, project, page, ai_says(model_item(after=body)))
    assert check(stored_meta(db)[1], "keyword_repetition")["status"] == "warning"


def test_text_already_on_the_page_is_flagged_as_duplication_risk_and_blocked(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    optimize(db, project, page, ai_says(model_item(after="## Eligibility\n\n" + SENTENCE)))
    _, meta = stored_meta(db)
    assert check(meta, "duplication")["status"] == "blocked" and "already on the page" in check(meta, "duplication")["message"]
    assert meta.validation_status == "blocked"


def test_text_copied_from_another_page_of_the_site_is_flagged(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    elsewhere = ("Prospective students must complete an entrance examination followed by a counselling round and must present "
                 "original mark sheets during document verification.")           # on the OTHER page only, not on the target page
    db.add(models.Page(project_id=project.id, url="https://mine.com/other-page", source="crawler", title="Other", custom_content="Intro. " + elsewhere + " More."))
    db.commit()
    optimize(db, project, page, ai_says(model_item(after="## Admission process\n\n" + elsewhere)))
    d = check(stored_meta(db)[1], "duplication")
    assert d["status"] == "blocked" and "https://mine.com/other-page" in d["message"] and "already on the page" not in d["message"]
    assert d["details"]["page"] == "https://mine.com/other-page"


def test_factual_claims_are_flagged_listed_and_never_marked_verified(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    optimize(db, project, page, ai_says(model_item(after="## Documents required\n\nThe application fee is 5000 rupees and the last date is 15 March 2026 for all applicants.", claims_to_verify=["Fee amount"])))
    _, meta = stored_meta(db)
    assert meta.validation_status == "needs_human_verification" and meta.requires_fact_check is True
    assert "Fee amount" in meta.claims_to_verify and any("5000" in c for c in meta.claims_to_verify)
    assert check(meta, "fact_check")["status"] == "needs_human_verification"
    assert "verified" not in {c["status"] for c in meta.validation_json["checks"]}


def test_product_rule_violations_and_unsafe_promises_are_caught_by_the_brand_check(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    optimize(db, project, page, ai_says(model_item(after="## Documents required\n\nGoogle requires every page to list its documents, and we offer guaranteed selection to all applicants.")))
    b = check(stored_meta(db)[1], "brand_tone")
    assert b["status"] == "blocked" and "Google requires" in b["message"]


def test_the_profiles_tone_and_the_pages_language_are_checked(db):
    project, page = make_world(db, lang="hi")
    db.add(models.BusinessProfile(project_id=project.id, brand="Acme", tone="formal and professional"))
    db.commit()
    make_evidence(db, project, page)
    optimize(db, project, page, ai_says(model_item(after="## Documents required\n\nBring your degree certificate and photo identity today! It is easy for every applicant.")))
    b = check(stored_meta(db)[1], "brand_tone")
    assert b["status"] == "warning" and "exclamation" in b["message"] and "not mostly" in b["message"]


def test_another_page_targeting_the_same_keyword_is_flagged_with_alternatives(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    db.add(models.Page(project_id=project.id, url="https://mine.com/b-ed-admission-guide", source="crawler", title="B.Ed Admission Guide", h1=["B.Ed admission"], h2=[]))
    db.commit()
    optimize(db, project, page, ai_says(model_item()))
    c = check(stored_meta(db)[1], "cannibalization")
    assert c["status"] == "warning" and c["details"]["alternatives"] == ["internal_link", "strengthen_existing_page", "separate_page"]
    assert "b-ed-admission-guide" in c["message"]


def test_competitor_wording_is_blocked(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    copied = "## Sports\n\nCompletely unrelated competitor text about campus sports facilities and hostel rooms for every student and more."
    optimize(db, project, page, ai_says(model_item(after=copied)))
    assert check(stored_meta(db)[1], "competitor_copy")["status"] == "blocked"


def test_an_internal_link_suggestion_must_use_a_supplied_page(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    db.add(models.Page(project_id=project.id, url="https://mine.com/who-can-apply", source="crawler", title="Who can apply", h1=["Who can apply"], h2=["Documents required"]))
    db.commit()
    good = 'Read who can apply and which <a href="https://mine.com/who-can-apply">documents are required</a> before you start.'
    optimize(db, project, page, ai_says(model_item("improve_internal_link", "sec_01", after=good, link_target="https://mine.com/who-can-apply", problem="No link to the documents page.")))
    s, meta = stored_meta(db)
    assert meta.link_target == "https://mine.com/who-can-apply" and meta.validation_status != "blocked"
    bad = 'Read <a href="https://evil.example.com/x">the documents</a> before you start the application form.'
    optimize(db, project, page, ai_says(model_item("improve_internal_link", "sec_02", after=bad, link_target="https://evil.example.com/x", problem="No link.")))
    assert db.query(models.SuggestionOptimization).filter_by(target_ref="sec_02").one().validation_status == "blocked"


def test_types_the_page_cannot_support_are_refused_not_guessed(db):
    project, page = make_world(db, markdown=None, heading_structure=[{"tag": "h2", "text": "Eligibility"}])
    make_evidence(db, project, page)
    run, _ = optimize(db, project, page, ai_says(model_item("rewrite_section", "sec_01", after="A clearer rewrite of the eligibility text goes here now.")))
    assert run.status == "error" and "not available for this page" in run.error and rows(db) == []


# ── regeneration and duplicate protection (the existing behaviour, extended) ─

def add_kind(text, label="Documents required"):
    return model_item(after=f"## {label}\n\n{text}")


def test_regeneration_replaces_undecided_suggestions_and_keeps_decided_ones(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    optimize(db, project, page, ai_says(add_kind("First proposal about documents and certificates that applicants should carry along.")))
    first = rows(db)[0][0]
    first.status, first.accepted_at = "accepted", datetime.now(timezone.utc)
    db.commit()
    optimize(db, project, page, ai_says(add_kind("Second proposal listing the certificates and photographs an applicant must bring on the day.")))
    assert sorted(s.status for s, _, _ in rows(db)) == ["accepted", "pending"]
    optimize(db, project, page, ai_says(add_kind("Third proposal explaining which identity documents and certificates are acceptable at registration.")))
    stored = rows(db)
    assert sorted(s.status for s, _, _ in stored) == ["accepted", "pending"]                     # the second (pending) was REPLACED, the accepted one survived
    assert [s.content for s, _, _ in stored if s.status == "pending"][0].startswith("## Documents required\n\nThird proposal")
    assert db.get(models.Suggestion, first.id).status == "accepted"
    assert db.query(models.SuggestionOptimization).count() == 2                                   # no orphaned detail rows


@pytest.mark.parametrize("decided", ["accepted", "edited", "deployed"])
def test_wording_a_person_already_decided_on_is_not_offered_again(db, decided):
    project, page = make_world(db)
    make_evidence(db, project, page)
    text = "Bring your degree certificate, photographs and photo identity to the counter on the day of registration."
    optimize(db, project, page, ai_says(add_kind(text)))
    s = rows(db)[0][0]
    s.status = decided
    db.commit()
    run, _ = optimize(db, project, page, ai_says(add_kind(text)))
    assert run.status == "error" and "repeated wording" in run.error
    assert "already proposed" in run.notes["discarded"][0]["reason"] and decided in run.notes["discarded"][0]["reason"]
    assert len(rows(db)) == 1


def test_rejected_wording_is_not_resurfaced_but_new_wording_is(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    text = "Bring your degree certificate, photographs and photo identity to the counter on the day of registration."
    optimize(db, project, page, ai_says(add_kind(text)))
    rows(db)[0][0].status = "rejected"
    db.commit()
    run, _ = optimize(db, project, page, ai_says(add_kind(text), model_item("add_faq", "new", after="Which documents are needed? Bring your degree certificate and a photo identity card to the counter.")))
    assert run.status == "ok"
    kinds = sorted((s.status, m.suggestion_type) for s, m, _ in rows(db))
    assert kinds == [("pending", "add_faq"), ("rejected", "add_section")]
    assert any("already proposed" in d["reason"] for d in run.notes["discarded"])


def test_pending_suggestions_for_another_keyword_survive_a_regeneration(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    make_evidence(db, project, page, keyword="teacher training course")
    optimize(db, project, page, ai_says(add_kind("Proposal made for the first keyword about the documents that applicants should bring.")))
    optimize(db, project, page, ai_says(add_kind("Proposal made for the second keyword about the documents that applicants should bring along.")), keyword="teacher training course")
    optimize(db, project, page, ai_says(add_kind("Regenerated proposal for the second keyword listing the certificates required at registration.")), keyword="teacher training course")
    texts = sorted(s.content for s, _, _ in rows(db))
    assert len(texts) == 2 and any("first keyword" in t for t in texts) and any("Regenerated" in t for t in texts)


def test_one_issue_hosts_each_kind_of_suggestion_across_runs_and_rank_increments(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    optimize(db, project, page, ai_says(add_kind("Proposal one about documents that applicants should bring with them to the counter.")))
    rows(db)[0][0].status = "accepted"
    db.commit()
    optimize(db, project, page, ai_says(add_kind("Proposal two about the certificates and photographs that must accompany the form.")))
    issues = db.query(models.Issue).filter(models.Issue.rule == "opt_add_section").all()
    assert len(issues) == 1
    assert sorted(s.rank for s, _, _ in rows(db)) == [1, 2]


def test_the_optimizer_content_hash_equals_the_existing_suggestion_code(db):
    from app.routes import suggestions as legacy
    for text in ["Plain text", "  Mixed   CASE\ttext ", "बी एड प्रवेश", ""]:
        assert co.content_hash(text) == legacy.content_hash(text)


def test_only_what_the_existing_deployers_can_write_is_marked_deployable():
    from app.routes import wordpress
    assert set(co.DEPLOYABLE_CATEGORIES) <= set(wordpress.FIELD_DEPLOYERS)
    assert co.issue_key("improve_title", "title") == ("title", "opt_improve_title")
    assert co.issue_key("improve_meta_description", "meta_description") == ("meta_description", "opt_improve_meta_description")
    assert co.issue_key("improve_heading", "h1") == ("h1", "opt_improve_h1") and co.issue_key("improve_heading", "sec_02") == ("h2", "opt_improve_heading")
    for t in ["add_section", "expand_section", "rewrite_section", "add_faq", "improve_internal_link"]:
        assert co.issue_key(t, "new") == ("content", f"opt_{t}")
        assert "content" not in co.DEPLOYABLE_CATEGORIES and "h2" not in co.DEPLOYABLE_CATEGORIES


# ── reading ───────────────────────────────────────────────────────────────

def test_run_detail_describes_each_suggestion_for_the_review_ui(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    run, _ = optimize(db, project, page, ai_says(
        model_item(), model_item("improve_title", "title", label="title does not contain", after="B.Ed admission: the complete guide for teachers", problem="The title omits the keyword.")))
    d = co.run_detail(db, run)
    assert d["status"] == "ok" and d["summary_line"] == "2 suggestions to review" and d["evidence"]["reused"] is True and d["evidence"]["status"] == "ok"
    by = {v["type"]: v for v in d["suggestions"]}
    sec, title = by["add_section"], by["improve_title"]
    assert sec["evidence"][0]["text"] == "4 of 5 comparable ranking pages cover “Documents required”. Your page: missing."
    assert sec["deployable"] is False and "WordPress connector can only write" in sec["deploy_note"] and sec["category"] == "content"
    assert title["deployable"] is True and title["deploy_note"] is None and title["before"] == "Teacher training course"
    assert sec["ready"] is True and sec["status"] == "pending" and sec["final_text"] == sec["after"] and sec["live"] == {}
    assert [v["number"] for v in d["suggestions"]] == [1, 2]


def test_earlier_decisions_for_the_page_stay_visible_on_a_later_run(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    first, _ = optimize(db, project, page, ai_says(add_kind("The first proposal that a person accepted about documents and certificates.")))
    rows(db)[0][0].status = "accepted"
    db.commit()
    second, _ = optimize(db, project, page, ai_says(add_kind("A second and different proposal about the documents that applicants must carry.")))
    d = co.run_detail(db, second)
    assert len(d["suggestions"]) == 1 and [e["status"] for e in d["earlier_decisions"]] == ["accepted"]
    assert co.run_detail(db, first)["earlier_decisions"] == []            # the accepted one belongs to the first run; the second's pending one is not a decision


def test_evidence_lines_are_built_from_stored_numbers():
    e = lambda **k: {"type": "topic_consensus", "label": "Fees", "competitor_count": 3, "competitor_total": 5, "target_coverage": "partial", **k}
    assert co.evidence_text(e()) == "3 of 5 comparable ranking pages cover “Fees”. Your page: partial."
    assert co.evidence_text(e(type="question_consensus", label="How much?")) == "3 of 5 comparable ranking pages answer “How much?”. Your page: partial."
    assert co.evidence_text(e(type="query_coverage", label="b ed fees")).startswith("3 of 5 comparable ranking pages use the phrase")
    assert co.evidence_text({"type": "page_fact", "label": "The page has no meta description"}) == "The page has no meta description."
    assert co.evidence_text({"type": "intent", "label": "Search intent looks informational; this page reads as a product page"}) == "Search intent looks informational; this page reads as a product page."
    assert co.evidence_text({"type": "serp_titles", "label": "Titles of the top results: A | B"}) == "Titles of the top results: A | B"
    assert co.evidence_text(e(target_coverage="n/a")) == "3 of 5 comparable ranking pages cover “Fees”."


def test_recent_runs_and_the_page_selector(db):
    project, page = make_world(db)
    db.add(models.Page(project_id=project.id, url="https://www.mine.com/b-ed-admission/", source="dataforseo", title="Same page, other source"))
    db.add(models.Page(project_id=project.id, url="https://mine.com/fees", source="crawler", title="  Fees   page "))
    db.commit()
    make_evidence(db, project, page)
    optimize(db, project, page, ai_says(add_kind("A proposal about the documents that applicants must carry with the application form.")))
    optimize(db, project, page, ai_says(no_change_reason="No change recommended."))
    listing = co.recent_runs(db, project.id)
    assert [r["status"] for r in listing] == ["no_change", "ok"] and listing[1]["summary_line"] == "1 suggestion to review" and listing[0]["summary_line"] == co.NO_CHANGE
    pages = co.selectable_pages(db, project.id)
    assert [p["url"] for p in pages] == ["https://mine.com/b-ed-admission", "https://mine.com/fees"] and pages[1]["title"] == "Fees page"


# ── a person's edit is held to the same checks ────────────────────────────

def test_an_edit_is_revalidated_and_a_blocked_edit_cannot_be_approved(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    optimize(db, project, page, ai_says(model_item()))
    s, meta = stored_meta(db)
    assert co.blocking_reasons(db, s.id) is None
    stuffed = "## Documents required\n\n" + " ".join(["b ed admission"] * 8) + " " + " ".join(f"detail{i}" for i in range(30))
    v = co.revalidate(db, s, stuffed)
    assert v["status"] == "blocked"
    co.apply_validation(db, s.id, v)
    db.commit()
    reasons = co.blocking_reasons(db, s.id)
    assert reasons and reasons[0].startswith("keyword_repetition:")
    clean = co.revalidate(db, s, "## Documents required\n\nBring your degree certificate and photo identity to the counter.")
    assert clean["status"] == "ok" and clean["claims"] == []
    co.apply_validation(db, s.id, clean)
    db.commit()
    assert co.blocking_reasons(db, s.id) is None and db.get(models.SuggestionOptimization, meta.id).requires_fact_check is False


def test_an_internal_link_edit_is_checked_against_the_stored_link_target(db):
    project, page = make_world(db)
    make_evidence(db, project, page)
    db.add(models.Page(project_id=project.id, url="https://mine.com/who-can-apply", source="crawler", title="Who can apply", h1=["Who can apply"], h2=["Documents required"]))
    db.commit()
    good = 'Read who can apply and which <a href="https://mine.com/who-can-apply">documents are required</a> before you start.'
    optimize(db, project, page, ai_says(model_item("improve_internal_link", "sec_01", after=good, link_target="https://mine.com/who-can-apply", problem="No link.")))
    s, _ = stored_meta(db)
    assert co.revalidate(db, s, good.replace("who-can-apply", "somewhere-else"))["status"] == "blocked"
    assert co.revalidate(db, s, good)["status"] != "blocked"


def test_a_suggestion_that_is_not_an_optimizer_one_is_never_revalidated_or_blocked(db):
    project, page = make_world(db)
    issue = models.Issue(project_id=project.id, page_id=page.id, category="title", rule="too_short", message="m")
    db.add(issue)
    db.flush()
    s = models.Suggestion(project_id=project.id, page_id=page.id, issue_id=issue.id, content="Legacy", content_hash="x")
    db.add(s)
    db.commit()
    assert co.revalidate(db, s, "anything") is None and co.blocking_reasons(db, s.id) is None and co.is_optimizer_suggestion(db, s.id) is False
