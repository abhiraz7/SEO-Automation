"""
Action-plan validation. The model may only CITE evidence ids; the application
rebuilds every evidence item (label, counts) from its own data. These tests pin that
property, the deterministic guards, and draft validation. No model, no network.
"""
import json

import pytest

from app import schemas
from app.services import action_plan as ap


def gap(kind="topic", label="B.Ed eligibility", count=6, total=7, coverage="missing", confidence="high", gid=None):
    return {"gap_type": kind, "label": label, "competitor_count": count, "competitor_total": total,
            "target_coverage": coverage, "confidence": confidence, "id": gid}


EVIDENCE = ap.build_evidence_list([
    gap("topic", "B.Ed eligibility", 6, 7, "missing", "high", gid=11),
    gap("topic", "Documents required", 5, 7, "partial", "high", gid=12),
    gap("topic", "Age limit", 6, 7, "covered", "high", gid=13),
    gap("question", "What is the fee?", 2, 7, "missing", "low", gid=14),
])


def plan(*actions):
    return schemas.ModelActionPlan.model_validate({"actions": list(actions)})


def act(**kw):
    base = {"id": "a1", "type": "add", "priority": "high", "title": "Add eligibility section",
            "problem": "The page does not cover eligibility.", "recommendation": "Add a section that explains who can apply.",
            "evidence_ids": ["E01"], "confidence": "high", "requires_fact_check": False}
    base.update(kw)
    return base


# ── evidence list ─────────────────────────────────────────────────────────

def test_evidence_ids_types_and_gap_ids():
    assert [e["id"] for e in EVIDENCE] == ["E01", "E02", "E03", "E04"]
    assert [e["type"] for e in EVIDENCE] == ["topic_consensus"] * 3 + ["question_consensus"]
    assert [e["gap_id"] for e in EVIDENCE] == [11, 12, 13, 14]
    assert ap.build_evidence_list([gap("query"), gap("intent"), gap("format")])[1]["type"] == "intent"
    assert ap.build_evidence_list([gap("format")])[0]["type"] == "serp_format"


# ── parsing ───────────────────────────────────────────────────────────────

GOOD_JSON = json.dumps({"actions": [act()]})


def test_parse_plain_json():
    assert ap.parse_action_plan(GOOD_JSON).actions[0].type == "add"


def test_parse_json_in_a_code_fence():
    assert len(ap.parse_action_plan("```json\n" + GOOD_JSON + "\n```").actions) == 1


def test_parse_json_surrounded_by_prose():
    assert len(ap.parse_action_plan("Here is the plan:\n" + GOOD_JSON + "\nHope that helps!").actions) == 1


def test_extra_fields_are_ignored():
    obj = {"actions": [{**act(), "invented_metric": 99, "competitor_count": 100}], "note": "x"}
    parsed = ap.parse_action_plan(json.dumps(obj))
    assert not hasattr(parsed.actions[0], "competitor_count")


@pytest.mark.parametrize("raw", ["", "not json", "[1, 2]", '{"actions": "nope"}', '{"nothing": 1}',
                                 json.dumps({"actions": [act(type="delete_everything")]}),
                                 json.dumps({"actions": [{"type": "add", "recommendation": "x"}]})])
def test_unusable_responses_raise_a_format_error(raw):
    with pytest.raises(ap.PlanFormatError):
        ap.parse_action_plan(raw)


# ── the model cannot supply facts ─────────────────────────────────────────

def test_evidence_is_rebuilt_from_application_data_not_from_the_model():
    out = ap.validate_action_plan(plan(act()), EVIDENCE)
    ev = out["actions"][0]["evidence"][0]
    assert ev == {"type": "topic_consensus", "label": "B.Ed eligibility", "competitor_count": 6, "competitor_total": 7, "gap_id": 11}


def test_counts_written_by_the_model_are_ignored_entirely():
    a = act(evidence_ids=["E01"])
    a["evidence"] = [{"type": "topic_consensus", "label": "B.Ed eligibility", "competitor_count": 999, "competitor_total": 1000}]
    out = ap.validate_action_plan(plan(a), EVIDENCE)
    assert out["actions"][0]["evidence"][0]["competitor_count"] == 6


def test_unknown_evidence_ids_are_dropped_with_a_warning():
    out = ap.validate_action_plan(plan(act(evidence_ids=["E01", "E99"])), EVIDENCE)
    assert len(out["actions"][0]["evidence"]) == 1 and any("E99" in w for w in out["warnings"])


def test_an_action_that_cites_nothing_real_is_rejected():
    out = ap.validate_action_plan(plan(act(evidence_ids=["E99"]), act(id="a2", evidence_ids=[])), EVIDENCE)
    assert out["actions"] == [] and len(out["rejected"]) == 2 and out["status"] == "no_data"
    assert all("no evidence" in r["reason"] for r in out["rejected"])


def test_leave_unchanged_may_cite_nothing():
    out = ap.validate_action_plan(plan(act(type="leave_unchanged", evidence_ids=[], priority="low", title="Keep the age section")), EVIDENCE)
    assert out["actions"][0]["type"] == "leave_unchanged" and out["actions"][0]["evidence"] == []


def test_duplicate_citations_are_collapsed():
    out = ap.validate_action_plan(plan(act(evidence_ids=["E01", "E01", "E01"])), EVIDENCE)
    assert len(out["actions"][0]["evidence"]) == 1


# ── consistency with the page's actual coverage ───────────────────────────

def test_recommending_a_change_for_something_already_covered_is_rejected():
    out = ap.validate_action_plan(plan(act(evidence_ids=["E03"])), EVIDENCE)  # E03 = "Age limit", covered
    assert out["actions"] == [] and "already covered" in out["rejected"][0]["reason"]


def test_leave_unchanged_for_something_entirely_missing_is_rejected():
    out = ap.validate_action_plan(plan(act(type="leave_unchanged", evidence_ids=["E01"])), EVIDENCE)
    assert out["actions"] == [] and "missing" in out["rejected"][0]["reason"]


def test_leave_unchanged_for_a_covered_item_is_accepted():
    out = ap.validate_action_plan(plan(act(type="leave_unchanged", evidence_ids=["E03"], priority="low", title="Age limit is fine")), EVIDENCE)
    assert out["actions"][0]["type"] == "leave_unchanged"


def test_mixed_coverage_evidence_is_allowed_for_a_change():
    assert ap.validate_action_plan(plan(act(evidence_ids=["E01", "E03"])), EVIDENCE)["actions"]


# ── forbidden claims ──────────────────────────────────────────────────────

@pytest.mark.parametrize("text,reason_part", [
    ("Google requires a section on eligibility.", "Google requires"),
    ("Search engines demand this topic be covered.", "Google requires"),
    ("This is mandatory for ranking.", "required for ranking"),
    ("Increase the keyword density to 3%.", "keyword-density"),
    ("Use keyword stuffing in the intro.", "stuffing"),
    ("Reach at least 2,000 words for this section.", "word-count"),
    ("Aim for a word count of 1500.", "word-count"),
    ("Copy the content from competitors into your page.", "copying"),
])
def test_forbidden_claims_reject_the_action(text, reason_part):
    out = ap.validate_action_plan(plan(act(recommendation=text)), EVIDENCE)
    assert out["actions"] == [] and reason_part in out["rejected"][0]["reason"]


def test_forbidden_claims_are_also_caught_in_the_title_and_problem():
    assert ap.validate_action_plan(plan(act(title="Google requires this")), EVIDENCE)["actions"] == []
    assert ap.validate_action_plan(plan(act(problem="Keyword density is too low")), EVIDENCE)["actions"] == []


def test_the_approved_wording_passes():
    text = "6 of 7 comparable ranking pages cover this topic and yours does not."
    assert ap.validate_action_plan(plan(act(problem=text)), EVIDENCE)["actions"]


# ── priority, confidence, fact-check flag ─────────────────────────────────

def test_high_priority_needs_a_strong_pattern():
    weak = ap.validate_action_plan(plan(act(evidence_ids=["E04"], priority="high", confidence="high")), EVIDENCE)  # 2 of 7, low
    strong = ap.validate_action_plan(plan(act(evidence_ids=["E01"], priority="high")), EVIDENCE)                    # 6 of 7
    assert weak["actions"][0]["priority"] == "low" and any("priority lowered" in w for w in weak["warnings"])
    assert strong["actions"][0]["priority"] == "high"


def test_stated_confidence_never_exceeds_the_evidence():
    out = ap.validate_action_plan(plan(act(evidence_ids=["E04"], confidence="high")), EVIDENCE)
    assert out["actions"][0]["confidence"] == "low"
    out = ap.validate_action_plan(plan(act(evidence_ids=["E01"], confidence="low")), EVIDENCE)
    assert out["actions"][0]["confidence"] == "low"  # and it can be lower than the evidence


def test_factual_looking_recommendations_require_a_fact_check():
    with_year = ap.validate_action_plan(plan(act(recommendation="State that the last date is 30 June 2026.")), EVIDENCE)
    plain = ap.validate_action_plan(plan(act(recommendation="Add a section explaining who can apply.")), EVIDENCE)
    restructure = ap.validate_action_plan(plan(act(type="restructure", recommendation="Move the 3 sections above the fold.")), EVIDENCE)
    assert with_year["actions"][0]["requires_fact_check"] is True
    assert plain["actions"][0]["requires_fact_check"] is False
    assert restructure["actions"][0]["requires_fact_check"] is False  # no new facts are being added


def test_the_models_own_fact_check_flag_is_kept():
    assert ap.validate_action_plan(plan(act(requires_fact_check=True)), EVIDENCE)["actions"][0]["requires_fact_check"] is True


# ── shape, ordering, limits ───────────────────────────────────────────────

def test_ids_are_reassigned_sequentially_and_actions_ordered_by_priority():
    out = ap.validate_action_plan(plan(
        act(id="x", priority="low", evidence_ids=["E01"], title="Low one"),
        act(id="y", priority="high", evidence_ids=["E01"], title="High one"),
    ), EVIDENCE)
    assert [a["id"] for a in out["actions"]] == ["action_001", "action_002"]
    assert [a["title"] for a in out["actions"]] == ["High one", "Low one"]


def test_at_most_eight_actions():
    many = [act(id=str(i), title=f"Action {i}", evidence_ids=["E01"]) for i in range(12)]
    out = ap.validate_action_plan(plan(*many), EVIDENCE)
    assert len(out["actions"]) == ap.MAX_ACTIONS and any("kept the 8 strongest" in w for w in out["warnings"])


def test_whitespace_only_text_is_rejected_and_long_text_is_clipped():
    out = ap.validate_action_plan(plan(act(title="   ", recommendation="x")), EVIDENCE)
    assert out["actions"] == [] and "empty" in out["rejected"][0]["reason"]
    # Within the schema's limits but longer than we display: clipped, not rejected.
    long = ap.validate_action_plan(plan(act(title="T" * 250, recommendation="R" * 1900)), EVIDENCE)
    a = long["actions"][0]
    assert len(a["title"]) == 120 and len(a["recommendation"]) == 600


def test_text_beyond_the_schema_limit_is_a_malformed_response():
    with pytest.raises(ap.PlanFormatError):
        ap.parse_action_plan(json.dumps({"actions": [act(title="T" * 500)]}))


def test_the_output_has_exactly_the_documented_shape():
    a = ap.validate_action_plan(plan(act()), EVIDENCE)["actions"][0]
    assert set(a) == {"id", "type", "priority", "title", "problem", "recommendation", "evidence", "confidence", "requires_fact_check"}
    assert set(a["evidence"][0]) == {"type", "label", "competitor_count", "competitor_total", "gap_id"}


def test_no_actions_at_all_is_no_data_not_a_fake_plan():
    out = ap.validate_action_plan(plan(), EVIDENCE)
    assert out == {"status": "no_data", "actions": [], "rejected": [], "warnings": []}


# ── drafts ────────────────────────────────────────────────────────────────

def draft(text, claims=()):
    return schemas.ModelGapDraft(draft=text, claims_to_verify=list(claims))


GOOD_DRAFT = "Candidates must hold a bachelor's degree from a recognised university and meet the minimum marks set by the admitting institution."


def test_parse_gap_draft():
    assert ap.parse_gap_draft('{"draft": "hello world text", "claims_to_verify": ["x"]}').claims_to_verify == ["x"]
    with pytest.raises(ap.PlanFormatError):
        ap.parse_gap_draft('{"nothing": 1}')


def test_a_good_draft_passes_and_factual_looking_sentences_are_surfaced():
    text = GOOD_DRAFT + " The minimum marks are usually 50% for general candidates."
    out = ap.validate_draft(draft(text), [])
    assert out["ok"] is True and any("50%" in c for c in out["claims_to_verify"]) and out["warnings"]


def test_the_models_own_claims_are_kept_and_not_duplicated():
    out = ap.validate_draft(draft(GOOD_DRAFT + " Fees are 5000 rupees.", claims=["Fees are 5000 rupees."]), [])
    assert out["claims_to_verify"].count("Fees are 5000 rupees.") == 1


def test_a_draft_without_figures_needs_no_verification():
    out = ap.validate_draft(draft(GOOD_DRAFT), [])
    assert out["ok"] and out["claims_to_verify"] == [] and out["warnings"] == []


@pytest.mark.parametrize("text", ["", "too short", "Add more detail.", "add more details"])
def test_thin_drafts_are_refused(text):
    out = ap.validate_draft(schemas.ModelGapDraft.model_construct(draft=text, claims_to_verify=[]), [])
    assert out["ok"] is False and "thin" in out["error"]


def test_oversized_drafts_are_refused():
    out = ap.validate_draft(draft("word " * 1000), [])
    assert out["ok"] is False and "longer than" in out["error"]


def test_forbidden_claims_in_a_draft_are_refused():
    out = ap.validate_draft(draft(GOOD_DRAFT + " Google requires this exact section on every page."), [])
    assert out["ok"] is False and "Google requires" in out["error"]


def test_copying_a_run_of_competitor_words_is_refused():
    source = "the applicant must have completed graduation from a recognised university with at least fifty percent marks in aggregate"
    copied = "Note that the applicant must have completed graduation from a recognised university with at least fifty percent marks and more."
    out = ap.validate_draft(draft(copied + " " + GOOD_DRAFT), [source])
    assert out["ok"] is False and "consecutive words" in out["error"]


def test_a_short_overlap_is_fine():
    source = "you must have completed graduation from a recognised university"
    out = ap.validate_draft(draft("Candidates must hold a degree from a recognised university, having completed their graduation in any stream, before applying."), [source])
    assert out["ok"] is True


def test_longest_shared_run():
    a, b = ap._words("one two three four five"), ap._words("zero two three four nine")
    assert ap.longest_shared_run(a, b) == 3
    assert ap.longest_shared_run([], b) == 0 and ap.longest_shared_run(a, []) == 0


def test_hindi_drafts_are_measured_and_split_on_the_danda():
    text = "उम्मीदवार के पास मान्यता प्राप्त विश्वविद्यालय से स्नातक की डिग्री होनी चाहिए। न्यूनतम अंक 50% होने चाहिए।"
    out = ap.validate_draft(draft(text), [])
    assert out["ok"] is True and any("50%" in c for c in out["claims_to_verify"])
