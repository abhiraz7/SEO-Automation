"""
Deterministic gap analysis. Every count and coverage verdict is computed from the
pages supplied here; nothing depends on a model. Includes Hindi cases: the site this
runs on is Hindi/English, and a tokenizer that shreds Devanagari words would make
every verdict on those pages silently wrong.
"""
import pytest

from app.services import gap_analysis as ga


def page(pos=1, domain=None, headings=(), text="", questions=(), title="", h1="", url=None, word_count=None):
    domain = domain or f"site{pos}.com"
    return {
        "position": pos, "domain": domain, "url": url or f"https://{domain}/guide",
        "title": title, "h1": h1,
        "headings": [{"tag": "h2", "text": h} for h in headings],
        "text": text, "questions": list(questions),
        "word_count": word_count if word_count is not None else len(text.split()),
    }


def by_label(gaps, needle):
    return next(g for g in gaps if needle.lower() in g["label"].lower())


# ── normalisation ─────────────────────────────────────────────────────────

def test_stop_words_digits_and_plurals_are_folded():
    assert ga.normalize_tokens("The Documents required for 2026 admission") == frozenset({"document", "required", "admission"})
    assert ga.normalize_tokens("document") == ga.normalize_tokens("Documents")
    assert ga.normalize_tokens("") == frozenset() and ga.normalize_tokens(None) == frozenset()


def test_double_s_words_are_not_stripped():
    assert "class" in ga.normalize_tokens("class")


def test_hindi_words_survive_intact_including_vowel_signs_and_conjuncts():
    tokens = ga.normalize_tokens("बी.एड योग्यता और पात्रता के लिए")
    assert "योग्यता" in tokens and "पात्रता" in tokens        # not shredded into fragments
    assert "और" not in tokens and "के" not in tokens          # Hindi function words dropped


def test_hindi_and_english_topics_match_themselves():
    a = ga.normalize_tokens("बी.एड योग्यता")
    assert ga.similar(a, ga.normalize_tokens("योग्यता बी.एड"))
    assert not ga.similar(a, ga.normalize_tokens("आयु सीमा"))


@pytest.mark.parametrize("a,b,expected", [
    ("B.Ed eligibility criteria", "eligibility criteria for B.Ed", True),
    ("Documents required", "Required documents", True),
    ("Age limit", "Fee structure", False),
    ("", "anything", False),
    ("admission process steps", "admission process", True),   # short label inside a longer one
])
def test_similar(a, b, expected):
    assert ga.similar(ga.normalize_tokens(a), ga.normalize_tokens(b)) is expected


@pytest.mark.parametrize("total,expected", [(2, 2), (3, 2), (5, 2), (6, 3), (7, 3), (10, 4)])
def test_consensus_threshold(total, expected):
    assert ga.consensus_threshold(total) == expected


@pytest.mark.parametrize("count,total,expected", [
    (6, 7, "high"), (5, 7, "high"), (3, 5, "medium"), (2, 5, "low"), (2, 2, "low"), (3, 3, "medium"), (4, 4, "high"),
])
def test_confidence_for(count, total, expected):
    assert ga.confidence_for(count, total) == expected


# ── topics ────────────────────────────────────────────────────────────────

FIVE = [
    page(1, headings=["Eligibility criteria", "Documents required", "Introduction"]),
    page(2, headings=["B.Ed eligibility criteria", "Age limit", "FAQ"]),
    page(3, headings=["Eligibility Criteria", "Documents Required"]),
    page(4, headings=["Fees", "Documents required for admission"]),
    page(5, headings=["Syllabus", "Age limit"]),
]


def test_a_recurring_topic_the_target_lacks_is_a_missing_gap_with_computed_counts():
    target = page(0, headings=["Overview of the course"], text="A short intro about teaching courses.")
    gaps = ga.topic_gaps(target, FIVE, keyword="b ed admission")
    elig = by_label(gaps, "eligibility")
    assert (elig["competitor_count"], elig["competitor_total"]) == (3, 5)
    assert elig["target_coverage"] == "missing" and elig["gap_type"] == "topic"
    assert {c["domain"] for c in elig["evidence"]["competitors"]} == {"site1.com", "site2.com", "site3.com"}
    assert all(c["heading"] for c in elig["evidence"]["competitors"])
    docs = by_label(gaps, "documents")
    assert docs["competitor_count"] == 3


def test_topics_below_the_consensus_threshold_are_not_reported():
    labels = [g["label"].lower() for g in ga.topic_gaps(page(0), FIVE)]
    assert not any("syllabus" in l or "fees" in l for l in labels)  # only 1 of 5 each


def test_generic_headings_are_ignored():
    pages = [page(i, headings=["Introduction", "FAQ", "Conclusion", "Related posts"]) for i in range(1, 6)]
    assert ga.topic_gaps(page(0), pages) == []


def test_a_heading_that_is_only_the_keyword_is_not_a_topic():
    pages = [page(i, headings=["B.Ed admission"]) for i in range(1, 5)]
    assert ga.topic_gaps(page(0), pages, keyword="b ed admission") == []


def test_target_coverage_covered_partial_missing():
    covered = ga.topic_gaps(page(0, headings=["Eligibility criteria"]), FIVE)
    partial = ga.topic_gaps(page(0, headings=["Other"], text="here we explain the eligibility criteria in a paragraph"), FIVE)
    missing = ga.topic_gaps(page(0, headings=["Other"], text="nothing relevant"), FIVE)
    assert by_label(covered, "eligibility")["target_coverage"] == "covered"
    assert by_label(partial, "eligibility")["target_coverage"] == "partial"
    assert by_label(missing, "eligibility")["target_coverage"] == "missing"


def test_a_competitor_repeating_a_heading_counts_once():
    pages = [page(1, headings=["Age limit", "Age limit", "Age limit"]), page(2, headings=["Age limit"]), page(3, headings=["Other topic"])]
    gap = by_label(ga.topic_gaps(page(0), pages), "age limit")
    assert gap["competitor_count"] == 2


def test_topics_are_ordered_by_how_many_pages_cover_them():
    gaps = ga.topic_gaps(page(0), FIVE)
    counts = [g["competitor_count"] for g in gaps]
    assert counts == sorted(counts, reverse=True)


def test_fewer_than_two_pages_means_no_pattern():
    assert ga.topic_gaps(page(0), [page(1, headings=["A topic here"])]) == []
    assert ga.topic_gaps(page(0), []) == []


def test_hindi_topics_are_clustered_and_matched():
    pages = [page(i, headings=["बी.एड योग्यता", "आयु सीमा"]) for i in (1, 2, 3)]
    missing = ga.topic_gaps(page(0, headings=["परिचय"], text=""), pages)
    covered = ga.topic_gaps(page(0, headings=["बी.एड योग्यता"]), pages)
    assert len(missing) == 2 and all(g["competitor_count"] == 3 for g in missing)
    assert by_label(covered, "योग्यता")["target_coverage"] == "covered"


def test_limit():
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet"]
    pages = [page(i, headings=[f"{w} details" for w in words]) for i in range(1, 4)]
    assert len(ga.topic_gaps(page(0), pages)) == 10
    assert len(ga.topic_gaps(page(0), pages, limit=5)) == 5


# ── questions ─────────────────────────────────────────────────────────────

def test_a_paa_question_the_target_does_not_answer_is_a_gap_counted_from_the_pages():
    pages = [
        page(1, questions=["What is the age limit for B.Ed?"]),
        page(2, headings=["Age limit for B.Ed"]),
        page(3, text="the age limit for b ed is different per state"),
        page(4, text="unrelated content about fees"),
    ]
    gaps = ga.question_gaps(page(0, text="nothing here"), pages, paa=["What is the age limit for B.Ed?"])
    g = gaps[0]
    assert g["gap_type"] == "question" and g["evidence"]["in_people_also_ask"] is True
    assert (g["competitor_count"], g["competitor_total"]) == (3, 4) and g["target_coverage"] == "missing"


def test_a_paa_question_no_competitor_answers_is_still_reported_with_low_confidence():
    gaps = ga.question_gaps(page(0), [page(1), page(2)], paa=["Is there an entrance exam for B.Ed?"])
    assert gaps[0]["competitor_count"] == 0 and gaps[0]["confidence"] == "low"


def test_recurring_competitor_questions_count_even_without_paa_and_rare_ones_do_not():
    pages = [page(i, questions=["How long is the B.Ed course?"]) for i in (1, 2, 3)] + [page(4, questions=["Is a one-off question here?"]), page(5)]
    gaps = ga.question_gaps(page(0), pages, paa=[])
    labels = [g["label"] for g in gaps]
    assert any("How long" in l for l in labels) and not any("one-off" in l for l in labels)


def test_paa_questions_come_first():
    pages = [page(i, questions=["How long is the B.Ed course?"]) for i in (1, 2, 3)]
    gaps = ga.question_gaps(page(0), pages, paa=["Who can apply for a B.Ed?"])
    assert gaps[0]["evidence"]["in_people_also_ask"] is True


def test_question_covered_by_a_target_heading():
    gaps = ga.question_gaps(page(0, headings=["What is the age limit for B.Ed?"]), [page(1), page(2)], paa=["What is the age limit for B.Ed?"])
    assert gaps[0]["target_coverage"] == "covered"


def test_no_pages_and_no_paa_means_no_question_gaps():
    assert ga.question_gaps(page(0), [], paa=[]) == []


# ── keyword / query coverage ──────────────────────────────────────────────

def test_the_target_keyword_is_always_reported_with_its_state():
    pages = [page(i, title="B.Ed admission guide", text="b ed admission details") for i in (1, 2, 3)]
    missing = ga.query_gaps(page(0, text="unrelated"), pages, "b ed admission", [])
    covered = ga.query_gaps(page(0, title="B.Ed admission 2026"), pages, "b ed admission", [])
    partial = ga.query_gaps(page(0, title="Courses", text="you can find b ed admission info below"), pages, "b ed admission", [])
    assert missing[0]["target_coverage"] == "missing" and missing[0]["evidence"]["role"] == "target_keyword"
    assert covered[0]["target_coverage"] == "covered" and partial[0]["target_coverage"] == "partial"
    assert (missing[0]["competitor_count"], missing[0]["competitor_total"]) == (3, 3)


def test_recurring_related_searches_are_reported_and_rare_ones_and_questions_are_not():
    pages = [page(i, text="b ed syllabus and b ed fees overview") for i in (1, 2, 3)] + [page(4, text="other"), page(5, text="other")]
    gaps = ga.query_gaps(page(0, text="x"), pages, "b ed", ["b ed syllabus", "b ed rare term", "what is b ed?", "how to apply for b ed"])
    labels = [g["label"] for g in gaps]
    assert "b ed syllabus" in labels
    assert "b ed rare term" not in labels and "what is b ed?" not in labels and "how to apply for b ed" not in labels


def test_duplicate_related_searches_are_reported_once():
    pages = [page(i, text="b ed syllabus") for i in (1, 2, 3)]
    gaps = ga.query_gaps(page(0), pages, "b ed", ["b ed syllabus", "B.Ed Syllabus", "b ed syllabus"])
    assert [g["label"] for g in gaps if g["evidence"]["role"] == "related_search"] == ["b ed syllabus"]


# ── format ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kw,expected", [
    (dict(url="https://x.com/tools/emi-calculator", title="EMI Calculator"), "tool"),
    (dict(url="https://x.com/product/notes-pdf", title="Notes PDF"), "product"),
    (dict(url="https://x.com/a", title="Delhi vs Mumbai for teachers"), "comparison"),
    (dict(url="https://x.com/news/exam-date", title="Exam date announced"), "news"),
    (dict(url="https://x.com/a", title="10 best B.Ed colleges in India"), "list"),
    (dict(url="https://x.com/a", title="Complete guide to B.Ed eligibility"), "guide"),
    (dict(url="https://x.com/a", title="Welcome", text="short page", word_count=30), "landing"),
    (dict(url="https://x.com/a", title="Welcome", text="x " * 350, word_count=350), "other"),
])
def test_classify_page_format(kw, expected):
    p = page(1, **{k: v for k, v in kw.items() if k in ("url", "title", "text", "word_count")})
    assert ga.classify_page_format(p) == expected


def test_many_numbered_headings_make_a_list():
    p = page(1, headings=["1. First", "2. Second", "3. Third", "4. Fourth"], title="Colleges", text="x " * 100, word_count=100)
    assert ga.classify_page_format(p) == "list"


def test_format_distribution():
    pages = [page(1, title="A complete guide"), page(2, title="Another guide to study"), page(3, title="10 best courses")]
    assert ga.format_distribution(pages) == {"guide": 2, "list": 1}


# ── intent ────────────────────────────────────────────────────────────────

def test_informational_intent_with_signals():
    intent = ga.classify_intent("how to apply for b ed admission", {"features": {"people_also_ask": True, "featured_snippet": True}}, [page(i, title="Complete guide") for i in (1, 2, 3)])
    assert intent["label"] == "informational" and intent["confidence"] in ("medium", "high") and len(intent["signals"]) >= 3


def test_commercial_intent():
    assert ga.classify_intent("best b ed colleges reviews", {"features": {}}, [])["label"] == "commercial"


def test_transactional_intent_from_keyword_and_serp():
    intent = ga.classify_intent("buy ctet notes price", {"features": {"shopping": True, "ads": 3}}, [])
    assert intent["label"] == "transactional"


def test_unknown_intent_when_there_is_nothing_to_go_on():
    assert ga.classify_intent("xyzzy", {"features": {}}, []) == {"label": "unknown", "confidence": "low", "signals": []}


def test_intent_signals_explain_the_verdict():
    intent = ga.classify_intent("what is ctet", {"features": {}}, [])
    assert any("what" in s for s in intent["signals"])


# ── format / intent gaps ──────────────────────────────────────────────────

GUIDES = [page(i, title="Complete guide to it") for i in (1, 2, 3, 4)]


def test_a_format_gap_when_the_target_differs_from_the_dominant_format():
    target = page(0, url="https://mine.com/product/notes", title="Notes")
    gaps = ga.format_and_intent_gaps(target, GUIDES, {"label": "unknown", "confidence": "low"})
    g = gaps[0]
    assert g["gap_type"] == "format" and (g["competitor_count"], g["competitor_total"]) == (4, 4)
    assert g["evidence"]["dominant_format"] == "guide" and g["evidence"]["target_format"] == "product"


def test_no_format_gap_when_the_target_matches_or_the_sample_is_too_small():
    same = page(0, title="A complete guide")
    assert ga.format_and_intent_gaps(same, GUIDES, {"label": "unknown", "confidence": "low"}) == []
    assert ga.format_and_intent_gaps(page(0, url="https://m.com/product/x"), GUIDES[:2], {"label": "unknown", "confidence": "low"}) == []


def test_an_intent_gap_only_when_confident_and_the_format_does_not_serve_it():
    target = page(0, title="A complete guide")
    confident = {"label": "transactional", "confidence": "medium", "signals": ["s"]}
    weak = {"label": "transactional", "confidence": "low", "signals": []}
    assert any(g["gap_type"] == "intent" for g in ga.format_and_intent_gaps(target, [], confident))
    assert not any(g["gap_type"] == "intent" for g in ga.format_and_intent_gaps(target, [], weak))


# ── recommendations and the whole run ─────────────────────────────────────

@pytest.mark.parametrize("gap,expected", [
    ({"gap_type": "topic", "target_coverage": "covered", "evidence": {}}, "leave_unchanged"),
    ({"gap_type": "topic", "target_coverage": "partial", "evidence": {}}, "expand"),
    ({"gap_type": "topic", "target_coverage": "missing", "evidence": {}}, "add"),
    ({"gap_type": "question", "target_coverage": "missing", "evidence": {}}, "add"),
    ({"gap_type": "query", "target_coverage": "partial", "evidence": {"role": "target_keyword"}}, "rewrite"),
    ({"gap_type": "query", "target_coverage": "missing", "evidence": {"role": "target_keyword"}}, "add"),
    ({"gap_type": "format", "target_coverage": "missing", "evidence": {"target_format": "product"}}, "separate_page"),
    ({"gap_type": "format", "target_coverage": "missing", "evidence": {"target_format": "guide"}}, "restructure"),
])
def test_default_action(gap, expected):
    assert ga.default_action(gap) == expected


def test_analyse_end_to_end():
    target = page(0, url="https://mine.com/b-ed", title="B.Ed course", headings=["Overview"], text="an overview of the course")
    serp = {"features": {"people_also_ask": True}, "paa": ["What is the age limit for B.Ed?"], "related": ["b ed syllabus"], "depth": "full"}
    out = ga.analyse(target, FIVE, serp, "b ed admission")
    assert out["question_data_available"] is True and out["intent"]["label"] in ga._INTENT_MODIFIERS or out["intent"]["label"] == "unknown"
    kinds = {g["gap_type"] for g in out["gaps"]}
    assert {"topic", "question", "query"} <= kinds
    assert all(g["recommended_action"] in ga.ACTIONS for g in out["gaps"])
    assert all(g["competitor_total"] == len(FIVE) for g in out["gaps"] if g["gap_type"] in ("topic", "question", "query"))


def test_thin_serp_says_question_data_is_unavailable_not_absent():
    out = ga.analyse(page(0), FIVE, {"features": {}, "paa": [], "related": [], "depth": "thin"}, "b ed")
    assert out["question_data_available"] is False


def test_word_count_never_changes_the_content_verdicts():
    """Word count is context, not a target: two targets that differ ONLY in word count
    get identical topic / question / query gaps. (Format classification may read length
    to tell a thin landing page from a guide; that is a label, not an optimisation target.)"""
    a = page(0, headings=["Overview"], text="short text", word_count=50)
    b = page(0, headings=["Overview"], text="short text", word_count=5000)
    serp = {"features": {}, "paa": [], "related": [], "depth": "full"}

    def content(target):
        return [g for g in ga.analyse(target, FIVE, serp, "b ed")["gaps"] if g["gap_type"] in ("topic", "question", "query")]
    assert content(a) == content(b) and content(a)
