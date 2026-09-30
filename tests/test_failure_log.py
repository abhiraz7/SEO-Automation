"""
The failure-logging helper and the logging setup: one greppable line per failure, the
right level, values that stay on one line, and no secrets.
"""
import logging
import re
import subprocess
import sys

import pytest

from app.services import failure_log as fl

LOG = logging.getLogger("test_failure_log")


# ── the message ───────────────────────────────────────────────────────────

def test_the_event_name_comes_first_and_plain_values_are_unquoted():
    assert fl.format_event("optimizer.run_failed", run=12, project=3, status="error") == "optimizer.run_failed run=12 project=3 status=error"


def test_values_with_spaces_or_punctuation_are_quoted_so_the_line_can_still_be_parsed():
    m = fl.format_event("x.failed", reason="The search results could not be fetched: dataforseo: 402")
    assert m == 'x.failed reason="The search results could not be fetched: dataforseo: 402"'
    assert fl.format_event("x.failed", url="https://rival.com/guide?a=1") == 'x.failed url="https://rival.com/guide?a=1"'


def test_none_fields_are_left_out_and_booleans_and_numbers_are_plain():
    assert fl.format_event("e", a=None, ok=True, bad=False, n=0, ratio=0.5) == "e ok=true bad=false n=0 ratio=0.5"


def test_a_value_is_always_one_line_even_if_it_contains_newlines():
    m = fl.format_event("e", reason="line one\nline two\r\n\tline three")
    assert "\n" not in m and "\r" not in m and "\t" not in m and 'reason="line one line two line three"' in m


def test_a_long_value_is_truncated():
    m = fl.format_event("e", reason="x" * 1000)
    assert len(m) < 400 and m.endswith('…"')


def test_lists_are_joined_and_hindi_is_kept_readable():
    assert fl.format_event("e", checks=["duplication", "keyword_repetition"]) == 'e checks="duplication, keyword_repetition"'
    assert "बी एड प्रवेश" in fl.format_event("e", keyword="बी एड प्रवेश")


@pytest.mark.parametrize("name", ["api_key", "API_KEY", "token", "access_token", "password", "client_secret", "authorization", "credentials", "cookie"])
def test_a_field_whose_name_looks_like_a_secret_is_never_written(name):
    m = fl.format_event("e", **{name: "s3cr3t-value"}, run=1)
    assert "s3cr3t-value" not in m and f"{name}=[redacted]" in m and "run=1" in m


@pytest.mark.parametrize("name", ["keyword", "keys_found", "tokens_used", "monkey", "passwords_checked", "run", "cookies_seen"])
def test_ordinary_fields_that_merely_contain_the_letters_of_a_secret_word_are_still_logged(name):
    """The filter matches whole name-parts, not substrings: 'keyword' is an SEO field, not a key."""
    m = fl.format_event("e", **{name: "visible"})
    assert f"{name}=visible" in m and "[redacted]" not in m


# ── levels ────────────────────────────────────────────────────────────────

def test_failure_is_a_warning_with_the_event_and_the_logger_name(caplog):
    with caplog.at_level(logging.INFO, logger="test_failure_log"):
        fl.failure(LOG, "optimizer.ai_failed", run=7, reason="529 overloaded")
    rec, = caplog.records
    assert rec.levelno == logging.WARNING and rec.name == "test_failure_log" and rec.getMessage() == 'optimizer.ai_failed run=7 reason="529 overloaded"'


def test_crash_is_an_error_with_the_traceback(caplog):
    with caplog.at_level(logging.INFO, logger="test_failure_log"):
        try:
            raise RuntimeError("kaboom")
        except RuntimeError:
            fl.crash(LOG, "optimizer.run_crashed", run=7)
    rec, = caplog.records
    assert rec.levelno == logging.ERROR and rec.exc_info and "kaboom" in caplog.text and "Traceback" in caplog.text


def test_note_is_info_not_a_failure(caplog):
    with caplog.at_level(logging.INFO, logger="test_failure_log"):
        fl.note(LOG, "optimizer.no_change", run=7)
    assert [r.levelno for r in caplog.records] == [logging.INFO]


# ── the setup ─────────────────────────────────────────────────────────────

def _run(code: str) -> str:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL).stderr


def test_the_setup_gives_every_warning_a_timestamp_a_level_and_a_source():
    err = _run("import logging\nfrom app.logging_setup import configure_logging\nconfigure_logging()\nlogging.getLogger('demo').warning('boom happened')\n")
    assert re.search(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} WARNING demo: boom happened$", err.strip().splitlines()[-1]), err


def test_the_setup_hides_info_so_library_chatter_cannot_bury_a_failure():
    err = _run("import logging\nfrom app.logging_setup import configure_logging\nconfigure_logging()\nlogging.getLogger('demo').info('routine chatter')\nlogging.getLogger('demo').error('real failure')\n")
    assert "routine chatter" not in err and "ERROR demo: real failure" in err


def test_without_the_setup_a_warning_has_no_timestamp_or_level():
    """The problem this fixes: Python's fallback handler prints the bare message."""
    err = _run("import logging\nlogging.getLogger('demo').warning('boom happened')\n")
    assert err.strip() == "boom happened"
