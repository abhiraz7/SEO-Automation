"""issue_copy: the numbers must be measured from stored data and the copy must
never state a measurement it doesn't have."""
from types import SimpleNamespace

from app import audit, issue_copy


def _page(title=None, desc=None):
    return SimpleNamespace(title=title, meta_description=desc)


def test_title_facts_are_measured_from_the_stored_title():
    f = issue_copy.facts(_page(title="x" * 78), "title")
    assert f == {"length": 78, "min": audit.TITLE_MIN, "max": audit.TITLE_MAX, "over_by": 78 - audit.TITLE_MAX, "under_by": 0}


def test_short_title_reports_under_by_not_over_by():
    f = issue_copy.facts(_page(title="Hindi Archives"), "title")
    assert f["over_by"] == 0 and f["under_by"] == audit.TITLE_MIN - len("Hindi Archives")


def test_facts_none_when_nothing_to_measure_or_no_length_concept():
    assert issue_copy.facts(_page(title="   "), "title") is None
    assert issue_copy.facts(_page(title="abc"), "canonical") is None
    assert issue_copy.facts(None, "title") is None


def test_explain_fills_the_real_numbers_into_the_detail():
    e = issue_copy.explain("title", "too_long", "Title tag is longer than recommended.", _page(title="y" * 78))
    assert e["tag"] == "Too long" and "78 characters" in e["detail"]
    assert e["facts"]["over_by"] == 78 - audit.TITLE_MAX
    assert e["why"]


def test_explain_falls_back_to_the_message_when_the_measurement_is_missing():
    e = issue_copy.explain("title", "too_long", "Title tag is longer than recommended.", _page(title=None))
    assert e["detail"] == "Title tag is longer than recommended."   # no invented length


def test_unknown_rule_falls_back_to_message_and_humanised_tag():
    e = issue_copy.explain("h2", "poor_structure", "H2 structure is poor.", None)
    assert e["headline"] == "H2 structure is poor." and e["tag"] == "Poor structure"


def _page_with(title):
    return SimpleNamespace(title=title, meta_description="", h1=["Behaviour and Emotional Difficulties"])


def test_good_title_suggestion_passes_every_check():
    page = _page_with("Behaviour Attention Emotional Difficulties Primary Children DSSSB Notes 2026")
    checks = issue_copy.check_suggestion("title", "DSSSB 2026: Behaviour & Emotional Difficulties Notes", page)
    assert all(c["passed"] for c in checks)
    assert issue_copy.checks_summary(checks)["state"] == "ok"


def test_out_of_range_length_is_a_hard_failure():
    page = _page_with("Some existing title for this page")
    checks = issue_copy.check_suggestion("title", "x" * 90, page)
    assert issue_copy.checks_summary(checks)["state"] == "review"


def test_empty_suggestion_is_a_hard_failure():
    assert issue_copy.checks_summary(issue_copy.check_suggestion("title", "  ", _page_with("abc")))["state"] == "review"


def test_invented_number_is_a_soft_failure_not_a_hard_one():
    page = _page_with("Indian Polity Notes for BPSC TRE exam preparation")
    checks = issue_copy.check_suggestion("title", "Indian Polity Notes 2031 for BPSC TRE preparation", page)
    failed = [c for c in checks if not c["passed"]]
    assert [c["label"] for c in failed] == ["Adds no new numbers or years"]
    assert issue_copy.checks_summary(checks)["state"] == "partial"


def test_unchanged_suggestion_is_flagged():
    title = "Indian Polity Notes for BPSC TRE exam preparation"
    checks = issue_copy.check_suggestion("title", title, _page_with(title))
    assert any(c["label"] == "Differs from the current text" and not c["passed"] for c in checks)


def test_categories_without_a_range_only_get_the_emptiness_check():
    checks = issue_copy.check_suggestion("canonical", "https://example.com/", _page_with("t"))
    assert len(checks) == 1 and checks[0]["passed"]


def test_every_registered_rule_has_a_tag_and_copy_pair_is_well_formed():
    for (cat, rule), (headline, detail, why) in issue_copy.COPY.items():
        assert headline and detail and why
        assert issue_copy.rule_tag(cat, rule)
