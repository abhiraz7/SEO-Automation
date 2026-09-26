"""
The page as the optimizer sees it: addressable sections, honest about what text is
unknown, plus the site's other pages for duplication / cannibalization / link
candidates. Pure functions and an in-memory DB; no network.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.services import optimizer_page as op


def page(**kw):
    base = dict(url="https://mine.com/b-ed", lang="en", title="B.Ed admission 2026", meta_description="All about B.Ed admission.",
                h1=["B.Ed admission"], h2=[], heading_structure=None, markdown=None, fit_markdown=None, custom_content=None,
                word_count=None, internal_links=[])
    base.update(kw)
    return SimpleNamespace(**base)


MD = """# B.Ed admission

Intro paragraph about the course.

## Eligibility criteria

Candidates need a **bachelor's** degree from a [recognised university](https://x.com).

### Age limit

There is an age limit.

## Fees

The fees vary.

#### A deeper note

Stays inside Fees.
"""


# ── sections ──────────────────────────────────────────────────────────────

def test_markdown_is_sliced_into_h2_h3_sections_with_stable_ids():
    m = op.build_page_model(page(markdown=MD))
    assert [(s["id"], s["tag"], s["heading"]) for s in m["sections"]] == [
        ("sec_01", "h2", "Eligibility criteria"), ("sec_02", "h3", "Age limit"), ("sec_03", "h2", "Fees")]
    assert m["sections_have_text"] is True


def test_a_section_runs_to_the_next_h2_or_h3_and_keeps_deeper_headings_inside():
    m = op.build_page_model(page(markdown=MD))
    s = {x["id"]: x for x in m["sections"]}
    assert "bachelor's" in s["sec_01"]["text"] and "age limit" not in s["sec_01"]["text"].lower()
    assert "**bachelor's**" in s["sec_01"]["text"]        # the body is the page's own text, not a cleaned copy: "before" must be what is really there
    assert s["sec_02"]["text"] == "There is an age limit."
    assert "Stays inside Fees." in s["sec_03"]["text"] and "A deeper note" in s["sec_03"]["text"]


def test_markdown_decoration_is_stripped_from_headings():
    m = op.build_page_model(page(markdown="## **Who** can [apply](https://x.com)?\n\ntext text text"))
    assert m["sections"][0]["heading"] == "Who can apply?"


def test_an_h1_in_the_markdown_is_the_title_not_a_section_and_ends_the_previous_section():
    m = op.build_page_model(page(markdown="## First\n\nbody one\n\n# Title again\n\nnot part of first\n\n## Second\n\nbody two"))
    assert [s["heading"] for s in m["sections"]] == ["First", "Second"]
    assert "not part of first" not in m["sections"][0]["text"]


def test_without_markdown_sections_come_from_the_headings_and_their_text_is_unknown_not_empty():
    m = op.build_page_model(page(heading_structure=[{"tag": "h1", "text": "B.Ed"}, {"tag": "h2", "text": "Eligibility"}, {"tag": "h3", "text": "Age"}, {"tag": "h4", "text": "x"}]))
    assert [(s["id"], s["heading"], s["text"], s["words"]) for s in m["sections"]] == [("sec_01", "Eligibility", None, None), ("sec_02", "Age", None, None)]
    assert m["sections_have_text"] is False


def test_headings_fall_back_to_the_h1_and_h2_lists():
    m = op.build_page_model(page(h1=["Main"], h2=["One", "Two"], heading_structure=None))
    assert [h["text"] for h in m["headings"]] == ["Main", "One", "Two"]
    assert [s["heading"] for s in m["sections"]] == ["One", "Two"]


def test_a_very_long_section_is_capped_and_says_so():
    m = op.build_page_model(page(markdown="## Big\n\n" + ("word " * 2000)))
    s = m["sections"][0]
    assert len(s["text"]) == op.SECTION_TEXT_CAP and s["truncated"] is True
    assert s["words"] == 2000          # the section's REAL length, not the length of the capped excerpt


def test_hindi_headings_and_text_are_kept_whole():
    m = op.build_page_model(page(markdown="## पात्रता मानदंड\n\nअभ्यर्थी के पास स्नातक की डिग्री होनी चाहिए।"))
    assert m["sections"][0]["heading"] == "पात्रता मानदंड" and m["sections"][0]["words"] == 8


def test_only_sections_whose_whole_text_is_short_enough_are_editable():
    m = op.build_page_model(page(markdown="## Short\n\nA short body.\n\n## Long\n\n" + "word " * 400))
    assert [(s["heading"], s["editable"]) for s in m["sections"]] == [("Short", True), ("Long", False)]
    unknown = op.build_page_model(page(heading_structure=[{"tag": "h2", "text": "Eligibility"}]))
    assert unknown["sections"][0]["editable"] is False                    # unknown text is never editable


# ── the fields ────────────────────────────────────────────────────────────

def test_missing_title_meta_and_h1_are_none_not_empty_strings():
    m = op.build_page_model(page(title="  ", meta_description=None, h1=[], lang=""))
    assert m["title"] is None and m["meta_description"] is None and m["h1"] is None and m["h1_count"] == 0 and m["lang"] is None


def test_h1_may_be_a_string_a_list_or_have_several_values():
    assert op.build_page_model(page(h1="Single"))["h1"] == "Single"
    m = op.build_page_model(page(h1=["", "First", "Second"]))
    assert m["h1"] == "First" and m["h1_count"] == 2


def test_whitespace_in_fields_is_collapsed():
    assert op.build_page_model(page(title="  B.Ed \n  admission  "))["title"] == "B.Ed admission"


# ── is the content known at all? ──────────────────────────────────────────

def test_a_page_with_no_headings_and_almost_no_text_has_unknown_content():
    m = op.build_page_model(page(heading_structure=None, h1=[], h2=[], custom_content="short", word_count=3))
    assert m["content_known"] is False


def test_headings_alone_make_the_content_known_and_so_does_enough_text():
    assert op.build_page_model(page(h1=["Main"], h2=[], heading_structure=None, custom_content=None))["content_known"] is True
    assert op.build_page_model(page(h1=[], h2=[], heading_structure=None, custom_content="word " * 60, word_count=None))["content_known"] is True


def test_find_section():
    m = op.build_page_model(page(markdown=MD))
    assert op.find_section(m, "sec_02")["heading"] == "Age limit" and op.find_section(m, "sec_99") is None


# ── keyword facts ─────────────────────────────────────────────────────────

def test_keyword_facts_are_order_independent_and_per_field():
    m = op.build_page_model(page(title="Admission for B.Ed 2026", meta_description="Fees and dates", h1=["Course guide"]))
    f = op.keyword_facts(m, "b ed admission")
    assert f["title"] is True and f["meta_description"] is False and f["h1"] is False
    assert f["title_length"] == len("Admission for B.Ed 2026") and f["meta_length"] == len("Fees and dates")


def test_keyword_facts_for_missing_fields_and_a_keyword_of_only_stop_words():
    m = op.build_page_model(page(title=None, meta_description=None, h1=[]))
    assert op.keyword_facts(m, "b ed admission")["title"] is False and op.keyword_facts(m, "b ed admission")["title_length"] == 0
    assert op.keyword_facts(op.build_page_model(page()), "the of and")["title"] is False


# ── the rest of the site ──────────────────────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    s = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def add_pages(db, rows):
    a = models.Project(name="A", base_url="https://mine.com")
    b = models.Project(name="B", base_url="https://other.com")
    db.add_all([a, b])
    db.flush()
    for project, url, title, h1, h2, source in rows:
        db.add(models.Page(project_id=(a if project == "A" else b).id, url=url, title=title, h1=h1, h2=h2, source=source))
    db.commit()
    return a, b


def test_url_key_ignores_scheme_www_trailing_slash_query_and_case():
    assert op.url_key("https://www.Mine.com/B-Ed/?utm=1") == op.url_key("http://mine.com/b-ed") == "mine.com/b-ed"
    assert op.url_key("") == ""


def test_site_pages_excludes_the_target_under_every_spelling_and_source_and_other_projects(db):
    a, _ = add_pages(db, [
        ("A", "https://mine.com/b-ed", "target", [], [], "crawler"),
        ("A", "https://www.mine.com/b-ed/", "target again (other source)", [], [], "dataforseo"),
        ("A", "https://mine.com/fees", "Fees", [], [], "crawler"),
        ("A", "https://mine.com/fees/", "Fees duplicate spelling", [], [], "dataforseo"),
        ("B", "https://other.com/x", "not mine", [], [], "crawler"),
    ])
    urls = [p.url for p in op.site_pages(db, a.id, "https://mine.com/b-ed")]
    assert len(urls) == 1 and op.url_key(urls[0]) == "mine.com/fees"      # one spelling only; never the target, never another project


def test_site_pages_respects_the_limit(db):
    a, _ = add_pages(db, [("A", f"https://mine.com/p{i}", f"P{i}", [], [], "crawler") for i in range(10)])
    assert len(op.site_pages(db, a.id, "https://mine.com/none", limit=4)) == 4


def test_related_pages_finds_keyword_and_topic_matches_and_orders_keyword_first(db):
    a, _ = add_pages(db, [
        ("A", "https://mine.com/b-ed-admission-guide", "B.Ed Admission Guide", ["B.Ed admission"], ["Eligibility criteria"], "crawler"),
        ("A", "https://mine.com/eligibility", "Who can apply", [], ["Eligibility criteria for teachers"], "crawler"),
        ("A", "https://mine.com/contact", "Contact us", [], [], "crawler"),
    ])
    pages = op.site_pages(db, a.id, "https://mine.com/target")
    rel = op.related_pages(pages, "b ed admission", ["Eligibility criteria"])
    assert [r["url"] for r in rel] == ["https://mine.com/b-ed-admission-guide", "https://mine.com/eligibility"]
    assert {"type": "keyword"} in rel[0]["reasons"] and {"type": "topic", "label": "Eligibility criteria"} in rel[0]["reasons"]
    assert rel[1]["reasons"] == [{"type": "topic", "label": "Eligibility criteria"}]
    assert op.related_pages(pages, "unrelated thing", ["nothing at all"]) == []


def test_a_keyword_can_match_through_the_url_slug(db):
    a, _ = add_pages(db, [("A", "https://mine.com/b-ed-admission-2026", "Home", [], [], "crawler")])
    rel = op.related_pages(op.site_pages(db, a.id, "https://mine.com/t"), "b ed admission", [])
    assert [r["reasons"] for r in rel] == [[{"type": "keyword"}]]


def test_internal_link_candidates_exclude_pages_already_linked_and_are_capped(db):
    a, _ = add_pages(db, [("A", f"https://mine.com/eligibility-{i}", "Eligibility guide", [], ["Eligibility criteria"], "crawler") for i in range(8)])
    rel = op.related_pages(op.site_pages(db, a.id, "https://mine.com/t"), "x", ["Eligibility criteria"], limit=8)
    model = op.build_page_model(page(internal_links=["https://www.mine.com/eligibility-0/", "https://mine.com/other"]))
    cands = op.internal_link_candidates(rel, model, limit=5)
    assert len(cands) == 5 and all(c["url"] != "https://mine.com/eligibility-0" for c in cands)
    assert set(cands[0]) == {"url", "title"}
