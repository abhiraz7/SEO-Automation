"""The review UI's word diff: exact re-assembly, and no markup injection from either text."""
import pytest

from app.services import text_diff as td


def rebuild(ops, keep):
    return "".join(t for op, t in ops if op in ("equal", keep))


@pytest.mark.parametrize("before,after", [
    ("The fees vary by college.", "The fees vary by college and course."),
    ("Old title", "A completely different title"),
    ("", "Brand new section text"),
    ("Text that will be removed entirely", ""),
    ("same", "same"),
    ("line one\nline two\n\nline four", "line one\nline 2\n\nline four\nline five"),
    ("बी एड प्रवेश", "बी एड प्रवेश 2026"),
    (None, "only after"),
    ("only before", None),
])
def test_the_diff_reassembles_both_texts_exactly(before, after):
    ops = td.word_diff(before, after)
    assert rebuild(ops, "delete") == (before or "")
    assert rebuild(ops, "insert") == (after or "")


def test_a_pure_addition_is_all_insert_and_a_pure_removal_is_all_delete():
    assert td.word_diff("", "new text") == [("insert", "new text")]
    assert td.word_diff("old text", "") == [("delete", "old text")]
    assert td.word_diff("unchanged", "unchanged") == [("equal", "unchanged")]


def test_punctuation_is_its_own_token_so_added_words_do_not_re_show_the_old_last_word():
    assert td.word_diff("The fees vary by college.", "The fees vary by college and course.") == [
        ("equal", "The fees vary by college"), ("insert", " and course"), ("equal", ".")]


def test_a_devanagari_word_is_never_split_inside_and_its_diff_is_whole_word():
    ops = td.word_diff("पात्रता मानदंड", "पात्रता और मानदंड")
    assert ops == [("equal", "पात्रता "), ("insert", "और "), ("equal", "मानदंड")]
    assert td.word_diff("योग्यता", "योग्यता।") == [("equal", "योग्यता"), ("insert", "।")]


def test_a_changed_word_shows_as_a_delete_and_an_insert_around_the_equal_parts():
    assert td.word_diff("the fee is 500", "the fee is 600") == [("equal", "the fee is "), ("delete", "500"), ("insert", "600")]


def test_html_in_either_text_is_escaped_so_only_our_own_tags_remain():
    import re
    out = str(td.diff_html('<script>alert(1)</script> keep', '<img src=x onerror=alert(1)> keep'))
    assert "<script>" not in out and "<img" not in out and "onerror=alert(1)>" not in out
    assert set(re.findall(r"</?(\w+)", out)) <= {"ins", "del"}                 # the only tags are our own
    plain = re.sub(r"</?(ins|del)[^>]*>", "", out)                             # take our two tags away: what is left is all escaped text
    assert "<" not in plain and ">" not in plain and "&lt;" in plain and "&gt;" in plain
    assert "script" in plain and "onerror" in plain                              # nothing was dropped, only neutralised


def test_the_result_marks_deletions_and_insertions():
    html = str(td.diff_html("old value here", "new value here"))
    assert "<del" in html and "old" in html and "<ins" in html and "new" in html and "value here" in html


def test_ampersands_and_quotes_survive_escaping():
    assert "&amp;" in str(td.diff_html("Fees & Dates", "Fees & \"Dates\" 2026"))
