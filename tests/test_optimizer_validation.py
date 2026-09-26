"""
The optimizer's validation pipeline, check by check. Pure functions: every input is
passed in, nothing touches a database, the network or a model.
"""
from types import SimpleNamespace

import pytest

from app.services import gap_analysis
from app.services import optimizer_validation as ov


def page_model(**over):
    base = {"text": "", "lang": "en", "title": "B.Ed admission", "meta_description": "About the B.Ed course.", "internal_links": [], "h1": "B.Ed admission"}
    base.update(over)
    return base


def make_ctx(**over):
    base = {"keyword": "b ed admission", "page": page_model(), "site_pages": [], "related": [], "candidates": [],
            "competitor_texts": [], "competitor_domains": [], "profile": None}
    base.update(over)
    return base


EVID = [{"type": "topic_consensus", "label": "Eligibility criteria", "competitor_count": 4, "competitor_total": 5}]
BODY = ("## Eligibility criteria\n\nApplicants should hold a bachelor's degree from a recognised university and "
        "submit the completed application form before the closing date.")


def make_s(**over):
    base = {"type": "add_section", "target_ref": "new", "before": None, "after": BODY, "problem": "The page never says who can apply.",
            "evidence": EVID, "claims_to_verify": [], "requires_fact_check": False, "link_target": None}
    base.update(over)
    return base


def check(result, name):
    return next(c for c in result["checks"] if c["name"] == name)


def run(s=None, ctx=None, **s_over):
    return ov.validate_suggestion(make_s(**s_over) if s is None else s, ctx or make_ctx())


def status_of(name, s=None, ctx=None, **s_over):
    return check(run(s, ctx, **s_over), name)["status"]


# ── the pipeline ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("statuses,expected", [
    (["ok", "ok"], "ok"), (["ok", "warning"], "warning"), (["warning", "needs_human_verification"], "needs_human_verification"),
    (["needs_human_verification", "error"], "error"), (["error", "blocked"], "blocked"), (["blocked", "ok", "warning"], "blocked"), ([], "ok"),
])
def test_overall_status_is_the_worst_check(statuses, expected):
    assert ov.overall_status([{"status": s} for s in statuses]) == expected


@pytest.mark.parametrize("status,ready", [("ok", True), ("warning", True), ("needs_human_verification", True), ("error", False), ("blocked", False)])
def test_only_ok_warning_and_needs_verification_can_be_approved(status, ready):
    assert ov.is_ready_for_approval({"status": status}) is ready


def test_a_clean_suggestion_passes_every_check_in_order():
    r = run(after="## Eligibility criteria\n\nApplicants should hold a bachelor's degree from a recognised university and submit the form on time.")
    assert r["status"] == "ok"
    assert [c["name"] for c in r["checks"]] == ["schema", "structure", "keyword_repetition", "duplication", "competitor_copy", "fact_check", "brand_tone", "cannibalization"]
    assert all(c["status"] == "ok" for c in r["checks"]) and r["claims"] == []


def test_cannibalization_only_runs_for_edits_that_add_substantial_content():
    names = lambda r: [c["name"] for c in r["checks"]]
    assert "cannibalization" in names(run(type="add_section")) and "cannibalization" in names(run(type="add_faq", after="Who can apply? Anyone with a degree can apply to the course this year."))
    title = run(type="improve_title", target_ref="title", before="B.Ed", after="B.Ed admission guide and dates")
    assert "cannibalization" not in names(title)


def test_an_unsupported_type_is_blocked_and_shown_not_dropped():
    r = run(type="rewrite_entire_article")
    assert r["status"] == "blocked" and [c["name"] for c in r["checks"]] == ["schema"]
    assert "Unsupported suggestion type" in r["checks"][0]["message"] and "details" not in r["checks"][0]


def test_no_proposed_text_is_blocked_and_nothing_else_is_judged():
    r = run(after="   ")
    assert r["status"] == "blocked" and [c["name"] for c in r["checks"]] == ["schema"]


def test_a_check_that_crashes_is_reported_as_an_error_and_the_rest_still_run(monkeypatch):
    def boom(s, ctx):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(ov, "_RUNNERS", [("schema", ov._schema), ("structure", boom), ("fact_check", ov._fact_check)])
    r = run()
    assert [c["name"] for c in r["checks"]][:3] == ["schema", "structure", "fact_check"]
    assert check(r, "structure")["status"] == "error" and "kaboom" in check(r, "structure")["message"]
    assert r["status"] == "error" and not ov.is_ready_for_approval(r)


def test_blocked_beats_needs_human_verification():
    r = run(after="## Eligibility\n\n<script>alert(1)</script> The fee is 5000 rupees for every applicant to the course each year.")
    assert check(r, "fact_check")["status"] == "needs_human_verification" and r["status"] == "blocked"


# ── 1. schema ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("type_,target", [
    ("improve_title", "meta_description"), ("improve_meta_description", "title"), ("improve_heading", "title"),
    ("expand_section", "new"), ("rewrite_section", "h1"), ("add_section", "sec_01"), ("add_faq", "title"), ("improve_internal_link", "title"),
])
def test_a_target_that_does_not_fit_the_type_is_blocked(type_, target):
    r = run(type=type_, target_ref=target, before="Some current text that exists here on the page.", after="A long enough proposed replacement text for this edit type to pass.",
            link_target="https://mine.com/x")
    assert check(r, "schema")["status"] == "blocked" and "does not fit" in check(r, "schema")["message"]


@pytest.mark.parametrize("type_,target", [
    ("improve_title", "title"), ("improve_meta_description", "meta_description"), ("improve_heading", "h1"), ("improve_heading", "sec_02"),
    ("expand_section", "sec_01"), ("rewrite_section", "sec_03"), ("add_section", "new"), ("add_faq", "new"), ("improve_internal_link", "sec_02"), ("improve_internal_link", "new"),
])
def test_every_valid_type_target_pair_is_accepted(type_, target):
    assert ov._target_fits(type_, target)


@pytest.mark.parametrize("type_", ["expand_section", "rewrite_section"])
def test_expanding_or_rewriting_a_section_whose_text_is_unknown_is_blocked(type_):
    r = run(type=type_, target_ref="sec_01", before=None)
    assert check(r, "schema")["status"] == "blocked" and "not available" in check(r, "schema")["message"]


def test_a_missing_h1_can_get_a_proposal_but_a_section_heading_needs_something_to_improve():
    assert status_of("schema", type="improve_heading", target_ref="h1", before=None, after="B.Ed admission 2026 guide") == "ok"
    assert status_of("schema", type="improve_heading", target_ref="sec_01", before=None, after="Who can apply") == "blocked"


def test_a_suggestion_must_state_its_problem_and_cite_evidence():
    assert status_of("schema", problem="  ") == "blocked"
    r = run(evidence=[])
    assert check(r, "schema")["status"] == "blocked" and "cites no evidence" in check(r, "schema")["message"]


@pytest.mark.parametrize("after", ["Add more detail.", "add more details about this", "Include additional information", "Provide further content here"])
def test_a_stub_that_only_asks_for_more_detail_is_blocked(after):
    assert status_of("schema", after=after) == "blocked"


@pytest.mark.parametrize("thin", [
    "Add more detail.", "add more details about this", "Include additional information", "Provide further content here",
    "Add more detail to this description so it is better", "Expand further information about the admission process",
])
def test_the_thin_edit_pattern_matches_requests_for_more_detail(thin):
    assert ov._THIN.match(thin)


def test_a_thin_request_is_blocked_even_when_it_is_long_enough_to_pass_the_minimum_length():
    text = "Add more detail to this description so it is better"
    assert len(text) >= ov.MIN_CHARS["improve_meta_description"]
    r = run(type="improve_meta_description", target_ref="meta_description", before="Old", after=text)
    assert check(r, "schema")["status"] == "blocked" and "thin edit" in check(r, "schema")["message"]


def test_a_real_sentence_that_merely_contains_add_more_detail_is_not_thin():
    text = "Add more detail about the eligibility rules, the documents required and the fee structure for every applicant here"
    assert not ov._THIN.match(text)


@pytest.mark.parametrize("type_,target,n,expected", [
    ("improve_title", "title", 7, "blocked"), ("improve_title", "title", 8, "ok"),
    ("improve_meta_description", "meta_description", 39, "blocked"), ("improve_meta_description", "meta_description", 40, "ok"),
    ("add_section", "new", 79, "blocked"), ("add_section", "new", 80, "ok"),
])
def test_minimum_lengths_stop_stub_edits(type_, target, n, expected):
    assert status_of("schema", type=type_, target_ref=target, before="Something", after="x" * n) == expected


def test_a_no_op_edit_is_blocked_even_if_only_case_or_spacing_differs():
    r = run(type="improve_title", target_ref="title", before="B.Ed Admission Guide 2026", after="b.ed   admission guide 2026")
    assert check(r, "schema")["status"] == "blocked" and "identical" in check(r, "schema")["message"]


def test_text_that_reads_like_an_instruction_is_a_warning_not_a_pass():
    r = run(after="Add a section explaining the eligibility criteria and the documents needed for applicants.")
    assert check(r, "schema")["status"] == "warning" and "instruction" in check(r, "schema")["message"]


# ── 2. structure ──────────────────────────────────────────────────────────

def t_title(after, **kw):
    return status_of("structure", type="improve_title", target_ref="title", before="Old", after=after, **kw)


def test_title_length_boundaries_are_warnings_never_blocks():
    assert t_title("x" * 65) == "ok" and t_title("x" * 66) == "warning" and t_title("x" * 15) == "ok" and t_title("x" * 14) == "warning"


def test_a_title_must_be_one_line_of_plain_text():
    assert t_title("Line one\nLine two") == "blocked" and t_title("B.Ed <b>admission</b> guide") == "blocked"


def test_meta_description_length_and_plain_text():
    m = lambda after: status_of("structure", type="improve_meta_description", target_ref="meta_description", before="Old", after=after)
    assert m("x" * 165) == "ok" and m("x" * 166) == "warning" and m("x" * 70) == "ok" and m("x" * 69) == "warning"
    assert m("x" * 100 + "\n" + "y" * 20) == "blocked" and m("<p>" + "x" * 100) == "blocked"


def test_a_long_heading_is_a_warning():
    h = lambda n: status_of("structure", type="improve_heading", target_ref="sec_01", before="Old heading", after="x" * n)
    assert h(110) == "ok" and h(111) == "warning"


@pytest.mark.parametrize("bad", [
    "<script>alert(1)</script>", "<iframe src='x'></iframe>", "<form action='x'><input></form>",
    "<p onclick='x()'>text</p>", "<a href='javascript:alert(1)'>go</a>", "<img src='data:text/html;base64,AAAA'>",
])
def test_dangerous_html_in_content_is_blocked(bad):
    assert status_of("structure", after="## Heading\n\n" + bad + " some more words to be long enough.") == "blocked"


@pytest.mark.parametrize("bad", ["<div><p>text</p>", "<b><i>text</b></i>", "text</span> more", "<ul><li>a</li>"])
def test_html_that_is_not_well_formed_is_blocked(bad):
    r = run(after="## Heading\n\n" + bad + " and enough extra words to pass the length check.")
    assert check(r, "structure")["status"] == "blocked"


@pytest.mark.parametrize("ok_html", [
    "<h2>Heading</h2><p>one<p>two", "<ul><li>a<li>b</ul>", "<p>line<br>break<br/>done</p>", "<h2>Heading</h2><p>Use <strong>bold</strong> and <em>italic</em>.</p>",
    "<table><tr><td>a<td>b</table>", "5 < 6 and 7 > 3 is simple arithmetic that any reader can follow easily",
])
def test_valid_or_lenient_html_and_plain_text_pass_structure(ok_html):
    assert status_of("structure", after="## Heading\n\n" + ok_html + " with enough extra words so it is not thin at all.") in ("ok", "warning")
    r = run(after="## Heading\n\n" + ok_html + " with enough extra words so it is not thin at all.")
    assert "blocked" not in check(r, "structure")["message"] if "message" in check(r, "structure") else True


def test_an_h1_in_new_content_is_blocked_html_or_markdown():
    assert status_of("structure", after="<h1>Big title</h1><p>" + "words " * 20 + "</p>") == "blocked"
    assert status_of("structure", after="# Big title\n\n" + "words " * 20) == "blocked"


def test_skipped_heading_levels_warn_and_a_new_section_without_a_heading_warns():
    assert status_of("structure", after="## First\n\n" + "words " * 20 + "\n\n#### Deep\n\nmore words here") == "warning"
    assert status_of("structure", after="Plain paragraph of text with no heading " + "words " * 20) == "warning"
    assert status_of("structure", after="## Heading\n\n### Sub\n\n" + "words " * 20) == "ok"


def test_an_faq_entry_needs_a_question():
    a = lambda after: status_of("structure", type="add_faq", after=after)
    assert a("Who can apply? Any graduate with a recognised degree can apply this year for the course.") == "ok"
    assert a("Any graduate with a recognised degree can apply this year for the course and more words here.") == "warning"


CAND = [{"url": "https://mine.com/eligibility", "title": "Eligibility"}, {"url": "https://mine.com/fees", "title": "Fees"}]


def link_status(after, link_target="https://mine.com/eligibility"):
    return status_of("structure", ctx=make_ctx(candidates=CAND), type="improve_internal_link", target_ref="sec_01", before=None, after=after, link_target=link_target)


def test_an_internal_link_must_point_at_a_supplied_page_and_only_that_page():
    assert link_status('See the <a href="https://mine.com/eligibility">eligibility criteria</a> for details of who can apply.') == "ok"
    assert link_status("See the [eligibility criteria](https://mine.com/eligibility) for details of who can apply.") == "ok"
    assert link_status('See <a href="https://mine.com/eligibility">the eligibility rules</a> here.', link_target="https://evil.com/x") == "blocked"     # not a candidate
    assert link_status('See <a href="https://mine.com/fees">the fees page</a> here.') == "blocked"                                                       # candidate, but not the chosen one
    assert link_status("See the eligibility criteria for details of who can apply to the course this year.") == "blocked"                               # no link at all
    assert link_status('See <a href="https://mine.com/eligibility"> </a> for details of who can apply to the course.') == "blocked"                      # no anchor text


def test_an_invented_url_is_blocked_even_when_the_link_and_the_chosen_target_agree():
    """The model can only be trusted with URLs the tool supplied. Consistent use of an
    invented address must not pass just because href == link_target."""
    text = 'See <a href="https://invented.example.com/x">the eligibility rules</a> for who can apply to this course.'
    r = run(ctx=make_ctx(candidates=CAND), type="improve_internal_link", target_ref="sec_01", before=None, after=text, link_target="https://invented.example.com/x")
    c = check(r, "structure")
    assert c["status"] == "blocked" and "not one of the site pages the tool supplied" in c["message"]


def test_too_many_links_in_one_edit_warn():
    many = " ".join('<a href="https://mine.com/eligibility">rule %d</a>' % i for i in range(3)) + " are described here in detail."
    assert link_status(many) == "warning"


def test_parse_fragment_on_plain_text_finds_nothing():
    assert ov.parse_fragment("Just words, no markup.") == {"blocked": [], "heading_levels": [], "links": []}


# ── 3. keyword repetition ─────────────────────────────────────────────────

def test_keyword_counts_exact_and_normalized():
    assert ov.keyword_counts("B.Ed admission and b ed admission again", "b ed admission") == {"exact": 2, "normalized": 2}
    n = ov.keyword_counts("Our B.Ed Admissions guide", "b ed admission")
    assert n["exact"] == 0 and n["normalized"] == 1          # plural + case differ from the keyword, same content words in order
    assert ov.keyword_counts(None, "b ed admission") == {"exact": 0, "normalized": 0}
    assert ov.keyword_counts("admission b ed", "b ed admission")["normalized"] == 0    # a different order is a different phrase


def test_keyword_counting_works_on_hindi():
    assert ov.keyword_counts("बी एड प्रवेश की जानकारी। बी एड प्रवेश आज।", "बी एड प्रवेश")["exact"] == 2


def kw_body(n, filler=60):
    return "## Guide\n\n" + " ".join(["b ed admission"] * n) + " " + " ".join(f"unique{i}" for i in range(filler))


@pytest.mark.parametrize("n,expected", [(2, "ok"), (3, "warning"), (5, "warning"), (6, "blocked")])
def test_added_keyword_occurrences_warn_then_block(n, expected):
    assert status_of("keyword_repetition", after=kw_body(n)) == expected


def test_only_the_increase_counts_not_the_total():
    r = run(type="rewrite_section", target_ref="sec_01", before=kw_body(5), after=kw_body(6))
    k = check(r, "keyword_repetition")
    assert k["status"] == "ok" and k["details"]["normalized_before"] == 5 and k["details"]["normalized_after"] == 6


@pytest.mark.parametrize("after,expected", [
    ("B.Ed admission guide", "ok"), ("B.Ed admission guide: b ed admission dates", "warning"), ("b ed admission | b ed admission | b ed admission", "blocked"),
])
def test_a_title_repeating_the_keyword_warns_at_two_and_blocks_at_three(after, expected):
    assert status_of("keyword_repetition", type="improve_title", target_ref="title", before="Old title here", after=after) == expected


def test_a_repeated_phrase_is_flagged_even_without_the_keyword():
    text = "## Fees\n\n" + " ".join(["The application fee applies."] * 3) + " " + " ".join(f"other{i}" for i in range(40))
    k = check(run(after=text), "keyword_repetition")
    assert k["status"] == "warning" and "repeated" in k["message"] and k["details"]["repeated_phrases"]


def test_repetition_that_was_already_there_is_not_blamed_on_an_edit_that_does_not_add_to_it():
    r = run(type="rewrite_section", target_ref="sec_01", before=kw_body(5), after=kw_body(6))
    assert check(r, "keyword_repetition")["details"]["repeated_phrases"] == []
    added = run(type="rewrite_section", target_ref="sec_01", before=kw_body(1), after=kw_body(2) + " Registration closes. " * 4)
    phrases = check(added, "keyword_repetition")["details"]["repeated_phrases"]
    assert len(phrases) == 1 and phrases[0]["count"] == 4 and phrases[0]["phrase"].startswith("registration")      # repetition the edit ADDED is flagged


def test_there_is_no_density_rule_a_long_natural_text_with_a_couple_of_mentions_is_fine():
    text = "## Guide\n\nb ed admission is open. " + " ".join(f"word{i}" for i in range(150)) + " Later, the b ed admission process ends."
    assert status_of("keyword_repetition", after=text) == "ok"


def test_the_details_report_before_after_and_page_totals():
    d = check(run(after=kw_body(2)), "keyword_repetition")["details"]
    assert d["exact_before"] == 0 and d["exact_after"] == 2 and d["normalized_after"] == 2 and d["page_normalized_after"] == 2 and d["repeated_phrases"] == []


# ── 4. duplication ────────────────────────────────────────────────────────

SENTENCE = "Applicants must hold a bachelor's degree with at least fifty percent marks from a recognised university and must submit two recent photographs with the form"


TOKENS = gap_analysis.tokenize(SENTENCE)


def dup_page(**kw):
    return SimpleNamespace(url=kw.pop("url", "https://mine.com/other"), title=kw.pop("title", None), meta_description=kw.pop("meta_description", None),
                           fit_markdown=kw.pop("fit_markdown", None), custom_content=kw.pop("custom_content", None), markdown=kw.pop("markdown", None))


def test_text_already_on_the_page_is_blocked():
    ctx = make_ctx(page=page_model(text="Intro. " + SENTENCE + ". Contact us."))
    r = run(ctx=ctx, after="## Eligibility\n\n" + SENTENCE + ".")
    assert check(r, "duplication")["status"] == "blocked" and "already on the page" in check(r, "duplication")["message"]


def test_partial_overlap_with_the_page_warns():
    shared = " ".join(TOKENS[:18])                       # 1 heading word + 18 shared + 12 fresh = 31 words -> 27 shingles, 14 shared -> ~52%
    ctx = make_ctx(page=page_model(text=shared + " and then some other unrelated wording that continues on"))
    r = run(ctx=ctx, after="## Eligibility\n\n" + shared + " " + " ".join(f"fresh{i} original{i}" for i in range(6)))
    d = check(r, "duplication")
    assert d["status"] == "warning" and 0.4 <= d["details"]["own_page"] < 0.7


def test_a_rewrite_may_reuse_the_wording_of_the_section_it_replaces():
    ctx = make_ctx(page=page_model(text="Intro. " + SENTENCE + ". Contact us."))
    r = run(ctx=ctx, type="rewrite_section", target_ref="sec_01", before=SENTENCE + ".", after=SENTENCE + " and also students in their final year may apply.")
    assert check(r, "duplication")["status"] == "ok"


def test_a_near_copy_of_another_page_on_the_site_is_blocked_and_names_it():
    ctx = make_ctx(site_pages=[dup_page(url="https://mine.com/eligibility", custom_content="Intro. " + SENTENCE + ". More.")])
    r = run(ctx=ctx, after="## Eligibility\n\n" + SENTENCE + ".")
    d = check(r, "duplication")
    assert d["status"] == "blocked" and "https://mine.com/eligibility" in d["message"] and d["details"]["page"] == "https://mine.com/eligibility"


def test_a_partial_overlap_with_another_page_warns():
    shared = " ".join(TOKENS[:20])                       # 1 + 20 + 10 = 31 words -> 27 shingles, 16 shared -> ~59%
    ctx = make_ctx(site_pages=[dup_page(custom_content=shared + " plus different continuing words for this page")])
    r = run(ctx=ctx, after="## Eligibility\n\n" + shared + " " + " ".join(f"novel{i} phrase{i}" for i in range(5)))
    d = check(r, "duplication")
    assert d["status"] == "warning" and 0.5 <= d["details"]["ratio"] < 0.8


def test_unrelated_text_and_unrelated_pages_pass_and_the_site_index_is_built_once():
    ctx = make_ctx(site_pages=[dup_page(custom_content="Completely different content about fees, hostels, campus life and sports facilities offered here")])
    first = run(ctx=ctx, after="## Eligibility\n\n" + SENTENCE + ".")
    assert check(first, "duplication")["status"] == "ok"
    index = ctx["_site_index"]
    run(ctx=ctx, after="## Eligibility\n\n" + SENTENCE + " again.")
    assert ctx["_site_index"] is index


def test_a_very_short_edit_is_not_compared():
    d = check(run(type="add_faq", after="Who can apply? Graduates can apply now for this year."), "duplication")
    assert d["status"] == "ok" and "Too short" in d.get("message", "")


@pytest.mark.parametrize("type_,target,field,label", [("improve_title", "title", "title", "title"), ("improve_meta_description", "meta_description", "meta_description", "meta description")])
def test_a_title_or_meta_used_verbatim_on_another_page_warns(type_, target, field, label):
    text = "B.Ed admission 2026 complete guide and important dates for applicants, everything you need to know"
    ctx = make_ctx(site_pages=[dup_page(url="https://mine.com/dup", **{field: text.upper()})])
    d = check(run(ctx=ctx, type=type_, target_ref=target, before="Old value here", after=text), "duplication")
    assert d["status"] == "warning" and "mine.com/dup" in d["message"] and label in d["message"]
    ctx2 = make_ctx(site_pages=[dup_page(**{field: "Something else entirely"})])
    assert check(run(ctx=ctx2, type=type_, target_ref=target, before="Old value here", after=text), "duplication")["status"] == "ok"


# ── 5. competitor wording ─────────────────────────────────────────────────

def test_without_competitor_text_the_check_says_it_did_not_compare():
    c = check(run(), "competitor_copy")
    assert c["status"] == "ok" and "not compared" in c["message"]


def test_a_long_run_of_competitor_words_is_blocked_a_shorter_one_warns():
    words = SENTENCE.split()
    long_run = " ".join(words[:14])
    body = "## H\n\n" + long_run + " " + " ".join(f"mine{i}" for i in range(30))
    blocked = check(run(after=body, ctx=make_ctx(competitor_texts=["Intro. " + SENTENCE])), "competitor_copy")
    assert blocked["status"] == "blocked" and blocked["details"]["longest_shared_run"] >= 12
    body2 = "## H\n\n" + " ".join(words[:9]) + " " + " ".join(f"mine{i}" for i in range(30))
    assert check(run(after=body2, ctx=make_ctx(competitor_texts=["Intro. " + SENTENCE])), "competitor_copy")["status"] == "warning"


def test_a_title_sharing_a_long_phrase_with_a_competitor_is_blocked():
    ctx = make_ctx(competitor_texts=["Complete B.Ed Admission Guide for 2026 Applicants"])
    r = run(ctx=ctx, type="improve_title", target_ref="title", before="Old", after="Complete B.Ed Admission Guide for 2026")
    assert check(r, "competitor_copy")["status"] == "blocked"


def test_original_wording_passes():
    unrelated = "Completely unrelated text about campus sports facilities and hostel rooms available to every student enrolled here"
    assert check(run(ctx=make_ctx(competitor_texts=[unrelated])), "competitor_copy")["status"] == "ok"


# ── 6. factual claims ─────────────────────────────────────────────────────

def test_a_new_figure_or_date_needs_human_verification_and_is_listed():
    r = run(after="## Fees\n\nThe application fee is 5000 rupees and the last date is 15 March 2026 for every applicant to the course.")
    f = check(r, "fact_check")
    assert f["status"] == "needs_human_verification" and r["claims"] and "5000" in r["claims"][0]


def test_figures_already_on_the_page_or_in_the_evidence_are_not_new_claims():
    ctx = make_ctx(page=page_model(text="The fee is 5000 rupees."))
    r = run(ctx=ctx, after="## Fees\n\nThe application fee is 5000 rupees for every applicant to the course each year and it is paid online.")
    assert check(r, "fact_check")["status"] == "ok"
    e = [{"type": "topic_consensus", "label": "Eligibility 12 criteria", "competitor_count": 4, "competitor_total": 5}]
    assert status_of("fact_check", evidence=e, after="## Eligibility\n\nThere are 12 criteria that every applicant should read before applying to the course.") == "ok"


def test_hindi_digits_are_detected_too():
    assert status_of("fact_check", after="## शुल्क\n\nआवेदन शुल्क ५००० रुपये है और अंतिम तिथि निकट है, सभी आवेदक ध्यान दें।") == "needs_human_verification"


@pytest.mark.parametrize("sentence", ["According to a recent report, most applicants prefer online forms.", "Studies show that early applicants do better.", "A survey of applicants found this."])
def test_citations_and_statistics_language_is_flagged(sentence):
    r = run(after="## Notes\n\n" + sentence + " Read the instructions carefully before you begin filling in the form.")
    assert check(r, "fact_check")["status"] == "needs_human_verification" and any(sentence.split(",")[0] in c for c in r["claims"])


def test_a_link_the_tool_did_not_supply_is_flagged_but_supplied_and_page_links_are_not():
    body = "## Links\n\nSee https://unknown.example.com/page for more details about the course and the admission process."
    r = run(after=body)
    assert check(r, "fact_check")["status"] == "needs_human_verification" and "unknown.example.com" in r["claims"][0]
    ctx = make_ctx(candidates=[{"url": "https://unknown.example.com/page", "title": "x"}])
    assert check(run(after=body, ctx=ctx), "fact_check")["status"] == "ok"
    ctx2 = make_ctx(page=page_model(text="Visit https://unknown.example.com/page today"))
    assert check(run(after=body, ctx=ctx2), "fact_check")["status"] == "ok"


def test_claims_from_the_model_are_kept_deduplicated_and_capped():
    model_claims = [f"Claim number {i}" for i in range(20)] + ["Claim number 0"]
    r = run(claims_to_verify=model_claims)
    assert len(r["claims"]) == ov.MAX_CLAIMS and r["claims"][0] == "Claim number 0" and len(set(r["claims"])) == len(r["claims"])


def test_the_model_flagging_facts_alone_is_enough_to_need_verification():
    r = run(requires_fact_check=True)
    f = check(r, "fact_check")
    assert f["status"] == "needs_human_verification" and f["details"]["model_flagged"] is True and r["claims"] == []


def test_no_claims_says_what_cannot_be_detected_and_nothing_is_ever_marked_verified():
    r = run(after="## Eligibility criteria\n\nApplicants should hold a bachelor's degree from a recognised university and submit the form on time.")
    f = check(r, "fact_check")
    assert f["status"] == "ok" and "cannot be detected automatically" in f["message"]
    assert "verified" not in {c["status"] for c in r["checks"]}


# ── 7. brand and tone ─────────────────────────────────────────────────────

def bt(after, ctx=None, **over):
    return check(run(ctx=ctx or make_ctx(), after=after, **over), "brand_tone")


def test_the_forbidden_claims_of_the_product_are_blocked():
    b = bt("## Notes\n\nGoogle requires a keyword density of 3 percent on every page of the site for ranking.")
    assert b["status"] == "blocked"


@pytest.mark.parametrize("text", ["We offer guaranteed selection for every student who joins the batch this year.", "The number one coaching institute for teacher training programmes.", "The best in India for teacher training courses of every kind."])
def test_unsafe_promises_and_unverifiable_superlatives_warn(text):
    assert bt("## About\n\n" + text)["status"] == "warning"


def test_a_language_mismatch_warns_and_a_matching_or_mixed_text_does_not():
    hi_ctx = make_ctx(page=page_model(lang="hi-IN"))
    english = "## Eligibility\n\nApplicants should hold a bachelor's degree from a recognised university to apply."
    assert bt(english, hi_ctx)["status"] == "warning" and "not mostly" in bt(english, hi_ctx)["message"]
    hindi = "## पात्रता\n\nआवेदक के पास किसी मान्यता प्राप्त विश्वविद्यालय से स्नातक की डिग्री होनी चाहिए।"
    assert bt(hindi, hi_ctx)["status"] == "ok"
    hinglish = "## पात्रता\n\nApplicants को स्नातक की डिग्री चाहिए, और आवेदन की अंतिम तिथि से पहले form जमा करना होगा।"
    assert bt(hinglish, hi_ctx)["status"] == "ok"
    assert bt(hindi, make_ctx(page=page_model(lang="en")))["status"] == "warning"


def test_the_expected_language_can_be_inferred_from_the_pages_own_text():
    hindi_page = make_ctx(page=page_model(lang=None, text="यह पृष्ठ बी एड पाठ्यक्रम के बारे में जानकारी देता है। " * 12))
    assert bt("## Eligibility\n\nApplicants should hold a bachelor's degree from a recognised university to apply.", hindi_page)["status"] == "warning"
    unknown = make_ctx(page=page_model(lang=None, text="short"))
    assert bt("## Eligibility\n\nApplicants should hold a bachelor's degree from a recognised university to apply.", unknown)["status"] == "ok"


def test_very_short_text_is_not_judged_on_language():
    ctx = make_ctx(page=page_model(lang="hi"))
    assert bt("Hello there", ctx, type="improve_heading", target_ref="h1", before="x")["status"] == "ok"


def formal():
    return make_ctx(profile=SimpleNamespace(tone="Formal and professional", brand="Acme"))


@pytest.mark.parametrize("text,fragment", [
    ("Apply now! Admissions close soon for the new session of this course.", "exclamation"),
    ("Apply now 🎓 admissions close soon for the new session of this course.", "emoji"),
    ("APPLY NOW before the FINAL DATE for admissions to this course at the college.", "ALL-CAPS"),
])
def test_a_formal_tone_flags_exclamations_emoji_and_shouting(text, fragment):
    b = bt("## Notice\n\n" + text, formal())
    assert b["status"] == "warning" and fragment in b["message"]


def test_other_tones_are_reported_as_not_checked_rather_than_passed_silently():
    casual = bt("## Hi\n\nJoin us now! It is going to be fun for everybody.", make_ctx(profile=SimpleNamespace(tone="friendly, casual")))
    assert casual["status"] == "ok" and "only formal tones can be checked" in casual["message"]
    none = bt("## Hi\n\nJoin us now! It is going to be fun for everybody.", make_ctx(profile=None))
    assert none["status"] == "ok" and "none set in the business profile" in none["message"]


def test_the_message_always_says_what_was_and_was_not_checked():
    m = bt("## Notice\n\nA plain sentence about the course and how to apply for it this year.", formal())["message"]
    assert "Checked:" in m and "unsafe claims" in m and "tone (formal)" in m and "forbidden phrases" in m and "audience" in m


def test_mentioning_a_competitor_by_name_warns_with_word_boundaries():
    ctx = make_ctx(competitor_domains=["www.rivalprep.com", "ab.in", "rival1.com"])
    assert bt("## Compare\n\nUnlike RivalPrep our course covers the whole syllabus for the entrance test.", ctx)["status"] == "warning"
    assert bt("## Compare\n\nOur course covers the whole syllabus for the entrance test, unlike ab tutorials.", ctx)["status"] == "ok"     # too short a name to match safely
    assert bt("## Compare\n\nOur course covers rival10 topics and the whole syllabus of the entrance test.", ctx)["status"] == "ok"      # 'rival1' must not match 'rival10'


# ── 8. cannibalization ────────────────────────────────────────────────────

def rel(url, reasons, title="T"):
    return {"page_id": 1, "url": url, "title": title, "reasons": reasons}


def test_another_page_targeting_the_keyword_warns_and_offers_alternatives():
    ctx = make_ctx(related=[rel("https://mine.com/b-ed-admission-guide", [{"type": "keyword"}])])
    c = check(run(ctx=ctx), "cannibalization")
    assert c["status"] == "warning" and "already targets this keyword" in c["message"] and "b-ed-admission-guide" in c["message"]
    assert c["details"]["alternatives"] == ["internal_link", "strengthen_existing_page", "separate_page"]
    assert c["details"]["pages"][0]["url"] == "https://mine.com/b-ed-admission-guide"


def test_a_topic_match_only_counts_when_it_is_the_topic_this_suggestion_cites():
    ctx = make_ctx(related=[rel("https://mine.com/fees", [{"type": "topic", "label": "Fees"}])])
    assert check(run(ctx=ctx), "cannibalization")["status"] == "ok"                                     # cites 'Eligibility criteria', not 'Fees'
    ctx2 = make_ctx(related=[rel("https://mine.com/eligibility", [{"type": "topic", "label": "Eligibility criteria"}])])
    c = check(run(ctx=ctx2), "cannibalization")
    assert c["status"] == "warning" and "already covers 'Eligibility criteria'" in c["message"]


def test_several_matches_are_counted_and_no_matches_pass():
    many = make_ctx(related=[rel("https://mine.com/a", [{"type": "keyword"}]), rel("https://mine.com/b", [{"type": "keyword"}])])
    assert "(and 1 more)" in check(run(ctx=many), "cannibalization")["message"]
    assert check(run(ctx=make_ctx()), "cannibalization")["status"] == "ok"


def test_cannibalization_never_blocks_it_hands_the_decision_to_a_person():
    ctx = make_ctx(related=[rel("https://mine.com/a", [{"type": "keyword"}])])
    assert run(ctx=ctx)["status"] in ("warning", "needs_human_verification")
