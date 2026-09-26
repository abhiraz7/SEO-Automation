"""
Evidence building, parsing the model's answer, and resolving it against the
application's own data: the model can cite evidence ids and name targets, but can
never supply a fact, a count or a "before" text. Pure functions; no DB, network or model.
"""
import json
from types import SimpleNamespace

import pytest

from app import schemas
from app.services import action_plan
from app.services import optimizer_page as op
from app.services import optimizer_plan as plan

MD = """# B.Ed admission

## Eligibility criteria

Applicants need a bachelor's degree.

## Fees

The fees vary by college.
"""


def make_page(**over):
    base = dict(url="https://mine.com/b-ed", lang="en", title="B.Ed admission 2026", meta_description="Everything about B.Ed admission.",
                h1=["B.Ed admission"], h2=[], heading_structure=None, markdown=MD, fit_markdown=None, custom_content=None,
                word_count=None, internal_links=[])
    base.update(over)
    return op.build_page_model(SimpleNamespace(**base))


def gap(gap_type, label, count, total, coverage="missing", conf="high", id_=None):
    return {"id": id_, "gap_type": gap_type, "label": label, "competitor_count": count, "competitor_total": total,
            "target_coverage": coverage, "confidence": conf}


GAPS = [
    gap("query", "b ed admission form", 3, 5, "partial", "medium", 4),
    gap("topic", "Documents required", 4, 5, "missing", "high", 2),
    gap("question", "Who can apply for B.Ed?", 3, 5, "missing", "medium", 3),
    gap("format", "guide", 5, 5, "partial", "high", 1),
]
CANDIDATES = [{"url": "https://mine.com/eligibility", "title": "Eligibility"}]


def make_bundle(page=None, gaps=None, candidates=CANDIDATES, titles=("Top result one", "Top result two")):
    page = page or make_page()
    evidence = plan.build_evidence(GAPS if gaps is None else gaps, page, "b ed admission", titles)
    return {"evidence": evidence, "page": page, "availability": plan.availability(page, candidates), "candidates": candidates}


def item(**over):
    base = {"type": "add_section", "target": "new", "priority": "medium", "problem": "The page never lists the documents required.",
            "evidence_ids": ["E03"], "after": "## Documents required\n\nBring your degree certificate and photo ID.", "claims_to_verify": [],
            "requires_fact_check": False, "confidence": "medium"}
    base.update(over)
    return schemas.ModelOptimizerSuggestion.model_validate(base)


def resolve(items, bundle=None, malformed=()):
    return plan.resolve_suggestions(items, list(malformed), bundle or make_bundle())


def evidence_id(bundle, label_part):
    return next(e["id"] for e in bundle["evidence"] if label_part in e["label"])


# ── evidence ──────────────────────────────────────────────────────────────

def test_evidence_follows_the_signal_priority_and_is_numbered_from_the_applications_data():
    ev = plan.build_evidence(GAPS, make_page(), "b ed admission", ["T1"])
    assert [e["type"] for e in ev[:4]] == ["serp_format", "topic_consensus", "question_consensus", "query_coverage"]
    assert [e["id"] for e in ev] == [f"E{i:02d}" for i in range(1, len(ev) + 1)]
    topic = next(e for e in ev if e["type"] == "topic_consensus")
    assert (topic["competitor_count"], topic["competitor_total"], topic["gap_id"], topic["target_coverage"]) == (4, 5, 2, "missing")


def test_intent_gaps_come_before_format_gaps():
    ev = plan.build_evidence([gap("format", "guide", 5, 5), gap("intent", "informational", 0, 5)], make_page(), "kw")
    assert [e["type"] for e in ev[:2]] == ["intent", "serp_format"]


def test_page_facts_are_computed_by_the_application():
    ev = plan.build_evidence([], make_page(title=None, meta_description="Nothing relevant here", h1=["Main", "Second"]), "b ed admission")
    facts = {e["label"]: e for e in ev if e["type"] == "page_fact"}
    assert facts["The page has no title"]["critical"] is True and facts["The page has no title"]["target_coverage"] == "missing"
    assert facts["The meta description does not contain the target keyword"]["critical"] is False
    assert "The page has 2 H1 elements" in facts


def test_a_page_whose_basics_are_in_place_has_no_page_facts():
    assert [e for e in plan.build_evidence([], make_page(), "b ed admission") if e["type"] == "page_fact"] == []
    ev = plan.build_evidence([], make_page(h1=[]), "b ed admission")
    assert any(e["label"] == "The page has no H1" and e["critical"] for e in ev)


def test_serp_titles_are_capped_truncated_and_omitted_when_there_are_none():
    ev = plan.build_evidence([], make_page(), "b ed admission", [f"Title {i} " + "x" * 200 for i in range(9)])
    t = next(e for e in ev if e["type"] == "serp_titles")
    titles = t["label"].split(": ", 1)[1].split(" | ")
    assert t["competitor_count"] == 5 and len(titles) == 5 and all(len(x) <= 90 for x in titles)
    assert not any(e["type"] == "serp_titles" for e in plan.build_evidence([], make_page(), "b ed admission", []))


def test_gap_evidence_is_capped():
    many = [gap("topic", f"Topic {i}", 3, 5) for i in range(40)]
    assert len([e for e in plan.build_evidence(many, make_page(), "kw") if e["type"] == "topic_consensus"]) == plan.MAX_GAP_EVIDENCE


def test_has_something_to_fix():
    covered = [gap("topic", "Fees", 4, 5, "covered")]
    assert plan.has_something_to_fix(plan.build_evidence(covered, make_page(), "b ed admission")) is False
    assert plan.has_something_to_fix(plan.build_evidence(covered + [gap("topic", "Documents", 4, 5, "missing")], make_page(), "b ed admission")) is True
    assert plan.has_something_to_fix(plan.build_evidence(covered, make_page(meta_description=None), "b ed admission")) is True       # a page fact
    assert plan.has_something_to_fix(plan.build_evidence([gap("format", "guide", 5, 5, "partial")], make_page(), "b ed admission")) is True


# ── what this page can support ────────────────────────────────────────────

def test_a_fully_crawled_page_supports_every_type():
    assert set(plan.available_types(make_page(), CANDIDATES)) == set(plan.ov.TYPES)


def test_a_page_without_section_text_cannot_be_expanded_or_rewritten():
    page = make_page(markdown=None, heading_structure=[{"tag": "h2", "text": "Eligibility"}])
    a = plan.availability(page, CANDIDATES)
    assert "not crawled" in a["expand_section"] and "not crawled" in a["rewrite_section"]
    assert a["add_section"] is None and a["improve_heading"] is None and a["improve_title"] is None


LONG_MD = "## Short section\n\nA short body.\n\n## Long section\n\n" + ("A long sentence that goes on and on. " * 60)


def test_a_section_too_long_for_one_atomic_edit_cannot_be_expanded_or_rewritten_but_the_short_one_can():
    page = make_page(markdown=LONG_MD)
    assert [s["editable"] for s in page["sections"]] == [True, False]
    b = make_bundle(page)
    ev = [evidence_id(b, "Documents required")]
    out = resolve([item(type="rewrite_section", target="sec_02", evidence_ids=ev, after="A shorter rewritten body " * 6),
                   item(type="rewrite_section", target="sec_01", evidence_ids=ev, after="A rewritten short body that says the same thing more clearly")], b)
    assert [s["target_ref"] for s in out["suggestions"]] == ["sec_01"]
    assert out["discarded"] == [{"id": "proposal 1", "reason": "that section is too long to expand or rewrite as one atomic edit"}]


def test_a_page_whose_only_sections_are_long_offers_no_expand_or_rewrite_but_still_allows_new_content():
    page = make_page(markdown="## Long section\n\n" + ("A long sentence that goes on and on. " * 60))
    a = plan.availability(page, CANDIDATES)
    assert a["expand_section"] and a["rewrite_section"] and a["add_section"] is None and a["add_faq"] is None


def test_unknown_content_blocks_content_types_but_not_title_and_meta():
    page = make_page(markdown=None, h1=[], h2=[], heading_structure=None, custom_content="short", word_count=2)
    a = plan.availability(page, CANDIDATES)
    assert a["add_section"] and a["add_faq"] and a["improve_internal_link"] and a["improve_heading"]
    assert a["improve_title"] is None and a["improve_meta_description"] is None


def test_no_link_candidates_means_no_internal_link_suggestions():
    assert "no other page" in plan.availability(make_page(), [])["improve_internal_link"]


def test_allowed_targets_list_the_fixed_fields_the_sections_and_new():
    assert plan.allowed_targets(make_page()) == ["title", "meta_description", "h1", "sec_01", "sec_02", "new"]


# ── parsing ───────────────────────────────────────────────────────────────

GOOD = {"suggestions": [{"type": "add_section", "target": "new", "priority": "high", "problem": "p", "evidence_ids": ["E01"], "after": "text", "confidence": "high"}],
        "no_change_reason": None}


def test_a_valid_answer_parses_including_code_fences_and_surrounding_prose():
    items, malformed, reason = plan.parse_output(json.dumps(GOOD))
    assert len(items) == 1 and items[0].type == "add_section" and malformed == [] and reason is None
    assert plan.parse_output("```json\n" + json.dumps(GOOD) + "\n```")[0][0].type == "add_section"
    assert plan.parse_output("Here you go:\n" + json.dumps(GOOD) + "\nHope that helps")[0][0].type == "add_section"


@pytest.mark.parametrize("raw", ["", "not json at all", "[1, 2, 3]", '{"suggestions": "none"}', '{"suggestions": {"a": 1}}', "{broken"])
def test_an_unusable_answer_raises_so_the_caller_can_retry(raw):
    with pytest.raises(action_plan.PlanFormatError):
        plan.parse_output(raw)


def test_one_malformed_suggestion_does_not_void_the_others():
    raw = json.dumps({"suggestions": [
        {"type": "add_section", "target": "new", "evidence_ids": "E01", "after": "x"},          # evidence_ids is not a list
        "just a string",
        {"type": "improve_title", "target": "title", "problem": "p", "evidence_ids": ["E01"], "after": "New title here"},
    ]})
    items, malformed, _ = plan.parse_output(raw)
    assert [i.type for i in items] == ["improve_title"]
    assert [m["id"] for m in malformed] == ["proposal 1", "proposal 2"] and all(m["reason"].startswith("malformed") for m in malformed)


def test_no_change_is_a_valid_answer_with_a_reason_that_is_kept_and_capped():
    items, malformed, reason = plan.parse_output(json.dumps({"suggestions": [], "no_change_reason": "No change recommended. " + "x" * 800}))
    assert items == [] and malformed == [] and reason.startswith("No change recommended.") and len(reason) == 500


def test_unknown_fields_are_ignored_and_defaults_are_conservative():
    items, _, _ = plan.parse_output(json.dumps({"suggestions": [{"type": "add_section", "before": "MODEL-SUPPLIED", "competitor_count": 99, "evidence_ids": ["E01"]}], "status": "ok"}))
    assert items[0].priority == "medium" and items[0].confidence == "low" and items[0].requires_fact_check is False
    assert not hasattr(items[0], "before") and not hasattr(items[0], "competitor_count")


# ── resolving ─────────────────────────────────────────────────────────────

def test_a_valid_suggestion_is_stored_with_evidence_rebuilt_from_the_applications_data():
    b = make_bundle()
    docs = evidence_id(b, "Documents required")
    out = resolve([item(evidence_ids=[docs])], b)
    s = out["suggestions"][0]
    assert s["id"] == "sug_001" and s["type"] == "add_section" and s["target_ref"] == "new" and s["before"] is None
    assert s["evidence"] == [{"type": "topic_consensus", "label": "Documents required", "competitor_count": 4, "competitor_total": 5, "target_coverage": "missing", "gap_id": 2}]
    assert out["discarded"] == [] and out["warnings"] == []


def test_the_model_cannot_supply_counts_or_a_before_text():
    raw = json.dumps({"suggestions": [{"type": "improve_title", "target": "title", "problem": "The title is generic.", "evidence_ids": ["E03"],
                                       "after": "B.Ed admission 2026: the complete guide", "before": "FAKE BEFORE", "competitor_count": 99, "competitor_total": 100}]})
    items, malformed, _ = plan.parse_output(raw)
    s = resolve(items, malformed=malformed)["suggestions"][0]
    assert s["before"] == "B.Ed admission 2026"                                  # the page's real title
    assert all(e["competitor_count"] <= e["competitor_total"] and e["competitor_count"] != 99 for e in s["evidence"])


def test_before_is_looked_up_per_target():
    b = make_bundle()
    ids = [evidence_id(b, "Documents required")]
    out = resolve([
        item(type="improve_title", target="title", evidence_ids=ids, after="A better title for the B.Ed admission page"),
        item(type="improve_meta_description", target="meta_description", evidence_ids=ids, after="x" * 100),
        item(type="improve_heading", target="h1", evidence_ids=ids, after="B.Ed admission: a complete guide"),
        item(type="improve_heading", target="sec_02", evidence_ids=ids, after="Fee structure"),
        item(type="expand_section", target="sec_01", evidence_ids=ids, after="Longer text about eligibility " * 5),
    ], b)
    rewrite = resolve([item(type="rewrite_section", target="sec_02", evidence_ids=ids, after="Clearer text about the fees " * 5)], b)
    by = {(s["type"], s["target_ref"]): s["before"] for s in out["suggestions"] + rewrite["suggestions"]}
    assert by[("improve_title", "title")] == "B.Ed admission 2026"
    assert by[("improve_meta_description", "meta_description")] == "Everything about B.Ed admission."
    assert by[("improve_heading", "h1")] == "B.Ed admission"
    assert by[("improve_heading", "sec_02")] == "Fees"
    assert "bachelor's degree" in by[("expand_section", "sec_01")]
    assert "fees vary" in by[("rewrite_section", "sec_02")]


def test_unknown_evidence_ids_are_dropped_and_a_suggestion_left_with_none_is_discarded():
    b = make_bundle()
    docs = evidence_id(b, "Documents required")
    out = resolve([item(evidence_ids=[docs, "E99"]), item(type="add_faq", evidence_ids=["E98"], after="Who can apply? Any graduate can apply this year for the course.")], b)
    assert len(out["suggestions"]) == 1 and len(out["suggestions"][0]["evidence"]) == 1
    assert any("E99" in w for w in out["warnings"])
    assert out["discarded"] == [{"id": "proposal 2", "reason": "it cites no evidence from the supplied list"}]


def test_a_suggestion_that_cites_nothing_is_discarded_with_its_reason():
    out = resolve([item(evidence_ids=[])])
    assert out["suggestions"] == [] and out["discarded"][0]["reason"] == "it cites no evidence from the supplied list"


def test_an_unsupported_type_is_discarded_individually_and_shown():
    b = make_bundle()
    out = resolve([item(type="rewrite_entire_article", evidence_ids=[evidence_id(b, "Documents")]), item(evidence_ids=[evidence_id(b, "Documents")])], b)
    assert [s["type"] for s in out["suggestions"]] == ["add_section"]
    assert out["discarded"] == [{"id": "proposal 1", "reason": "unsupported suggestion type 'rewrite_entire_article'"}]


def test_a_type_this_page_cannot_support_is_discarded_with_the_reason():
    page = make_page(markdown=None, heading_structure=[{"tag": "h2", "text": "Eligibility"}])
    b = make_bundle(page)
    out = resolve([item(type="rewrite_section", target="sec_01", evidence_ids=["E01"], after="Rewritten text " * 10)], b)
    assert out["suggestions"] == [] and "not available for this page" in out["discarded"][0]["reason"] and "not available" in out["discarded"][0]["reason"]


@pytest.mark.parametrize("type_,target", [("improve_title", "meta_description"), ("add_section", "sec_01"), ("expand_section", "new"), ("improve_heading", "sec_99"), ("improve_title", "nonsense")])
def test_a_target_that_does_not_fit_or_does_not_exist_is_discarded(type_, target):
    b = make_bundle()
    out = resolve([item(type=type_, target=target, evidence_ids=[evidence_id(b, "Documents")])], b)
    assert out["suggestions"] == [] and "does not fit" in out["discarded"][0]["reason"]


def test_missing_text_or_problem_is_discarded():
    b = make_bundle()
    ev = [evidence_id(b, "Documents")]
    out = resolve([item(after="   ", evidence_ids=ev), item(problem="  ", evidence_ids=ev)], b)
    assert [d["reason"] for d in out["discarded"]] == ["it proposes no text", "it does not say what the problem is"]


def test_a_problem_that_claims_google_requires_something_is_discarded():
    b = make_bundle()
    out = resolve([item(problem="Google requires a section on documents.", evidence_ids=[evidence_id(b, "Documents")])], b)
    assert out["suggestions"] == [] and "claims Google requires something" in out["discarded"][0]["reason"]


def test_adding_content_for_something_the_page_already_covers_is_discarded_but_a_title_edit_may_cite_it():
    covered = [gap("topic", "Fees", 4, 5, "covered", id_=7)]
    b = make_bundle(gaps=covered)
    fees = evidence_id(b, "Fees")
    out = resolve([item(evidence_ids=[fees]), item(type="improve_title", target="title", evidence_ids=[fees], after="A different B.Ed admission title")], b)
    assert [s["type"] for s in out["suggestions"]] == ["improve_title"]
    assert "already covered by the page" in out["discarded"][0]["reason"]


def test_an_expand_of_a_covered_topic_is_also_discarded():
    b = make_bundle(gaps=[gap("topic", "Fees", 4, 5, "covered", id_=7)])
    out = resolve([item(type="expand_section", target="sec_02", evidence_ids=[evidence_id(b, "Fees")], after="More about the fees " * 8)], b)
    assert out["suggestions"] == []


def test_high_priority_needs_strong_evidence():
    weak = [gap("topic", "Scholarships", 2, 5, "missing", "low", id_=9), gap("topic", "Hostels", 1, 6, "missing", "low", id_=10)]
    b = make_bundle(gaps=weak)
    out = resolve([item(priority="high", evidence_ids=[evidence_id(b, "Hostels")])], b)
    assert out["suggestions"][0]["priority"] == "low" and any("priority lowered" in w for w in out["warnings"])
    out2 = resolve([item(priority="high", evidence_ids=[evidence_id(b, "Scholarships")])], b)
    assert out2["suggestions"][0]["priority"] == "medium"                        # 2 of 5 = 0.4: not enough for high, enough for medium
    strong = make_bundle()
    assert resolve([item(priority="high", evidence_ids=[evidence_id(strong, "Documents")])], strong)["suggestions"][0]["priority"] == "high"


def test_a_missing_meta_description_is_strong_enough_evidence_for_high_priority():
    b = make_bundle(page=make_page(meta_description=None), gaps=[])
    fact = evidence_id(b, "no meta description")
    out = resolve([item(type="improve_meta_description", target="meta_description", priority="high", evidence_ids=[fact], after="x" * 100)], b)
    assert out["suggestions"][0]["priority"] == "high" and out["suggestions"][0]["before"] is None


def test_a_page_fact_that_is_not_a_missing_element_only_supports_medium():
    b = make_bundle(page=make_page(title="Guide to teaching courses"), gaps=[])
    fact = evidence_id(b, "title does not contain")
    out = resolve([item(type="improve_title", target="title", priority="high", evidence_ids=[fact], after="B.Ed admission guide 2026")], b)
    assert out["suggestions"][0]["priority"] in ("medium", "low")


def test_an_invalid_priority_becomes_medium_and_confidence_never_exceeds_the_evidence():
    b = make_bundle(gaps=[gap("topic", "Hostels", 3, 5, "missing", "low", id_=9)])
    out = resolve([item(priority="urgent!!", confidence="high", evidence_ids=[evidence_id(b, "Hostels")])], b)
    s = out["suggestions"][0]
    assert s["priority"] == "medium" and s["confidence"] == "low"


def test_the_same_type_and_target_is_kept_once_the_stronger_one():
    b = make_bundle()
    docs, q = evidence_id(b, "Documents required"), evidence_id(b, "Who can apply")
    out = resolve([item(priority="low", evidence_ids=[q], after="## A\n\nsome weaker suggestion text goes here for the section"),
                   item(priority="high", evidence_ids=[docs])], b)
    assert len(out["suggestions"]) == 1 and out["suggestions"][0]["priority"] == "high"
    assert "stronger suggestion" in out["discarded"][0]["reason"]


def test_at_most_five_suggestions_are_kept_strongest_first_and_numbered_in_order():
    gaps = [gap("topic", f"Topic {i}", 5 - (i % 3), 5, "missing", "high", id_=i) for i in range(1, 9)]
    b = make_bundle(gaps=gaps)
    # seven suggestions of seven different (type, target) pairs, so none collapses into another
    types = ["add_section", "add_faq", "improve_title", "improve_meta_description", "improve_heading", "expand_section", "rewrite_section"]
    targets = ["new", "new", "title", "meta_description", "h1", "sec_01", "sec_02"]
    items = [item(type=t, target=tg, evidence_ids=[evidence_id(b, f"Topic {i + 1}")], after=f"Proposed text number {i} " * 8, priority="medium")
             for i, (t, tg) in enumerate(zip(types, targets))]
    out = resolve(items, b)
    assert len(out["suggestions"]) == 5 and [s["id"] for s in out["suggestions"]] == [f"sug_{n:03d}" for n in range(1, 6)]
    assert any("kept the 5 strongest of 7" in w for w in out["warnings"]) and sum("over the limit" in d["reason"] for d in out["discarded"]) == 2
    # topics 1..7 have strengths 0.8 0.6 1.0 0.8 0.6 1.0 0.8: the two 0.6 ones (proposals 2 and 5) are the ones that go
    assert {d["id"] for d in out["discarded"]} == {"proposal 2", "proposal 5"}
    assert [s["type"] for s in out["suggestions"]][:2] == ["improve_title", "expand_section"]      # the two 1.0 ones lead


def test_link_target_is_kept_only_for_internal_link_suggestions_and_claims_are_cleaned():
    b = make_bundle()
    ev = [evidence_id(b, "Documents required")]
    out = resolve([
        item(type="improve_internal_link", target="sec_01", evidence_ids=ev, link_target=" https://mine.com/eligibility ", after='See <a href="https://mine.com/eligibility">eligibility</a>.'),
        item(evidence_ids=ev, link_target="https://mine.com/eligibility", claims_to_verify=["  fee is 5000  ", "", "x" * 400] + [f"c{i}" for i in range(20)]),
    ], b)
    by = {s["type"]: s for s in out["suggestions"]}
    assert by["improve_internal_link"]["link_target"] == "https://mine.com/eligibility" and by["add_section"]["link_target"] is None
    claims = by["add_section"]["claims_to_verify"]
    assert claims[0] == "fee is 5000" and len(claims) == plan.MAX_CLAIMS and len(claims[1]) == 300


def test_stored_suggestions_are_valid_strict_models():
    b = make_bundle()
    s = resolve([item(evidence_ids=[evidence_id(b, "Documents required")])], b)["suggestions"][0]
    assert schemas.OptimizerSuggestion.model_validate(s).type == "add_section"
    with pytest.raises(Exception):
        schemas.OptimizerSuggestion.model_validate({**s, "type": "rewrite_entire_article"})
    with pytest.raises(Exception):
        schemas.OptimizerSuggestion.model_validate({**s, "evidence": []})
    with pytest.raises(Exception):
        schemas.OptimizerSuggestion.model_validate({**s, "priority": "urgent"})
