"""Display helpers for the On-Page rows: they must derive labels from stored
data only and never invent a value."""
from types import SimpleNamespace

import pytest

from app.routes.onpage_semrush import measured_length, page_display_name


@pytest.mark.parametrize("url,expected", [
    ("https://example.com/", "Home"),
    ("https://example.com", "Home"),
    ("", "Home"),
    (None, "Home"),
    ("https://example.com/free-notes/indian-polity-notes/", "Indian polity notes"),
    ("https://example.com/about_us.html", "About us"),
    ("https://example.com/diploma/", "Diploma"),
    ("https://example.com/%E0%A4%B9%E0%A4%BF%E0%A4%82%E0%A4%A6%E0%A5%80", "हिंदी"),
])
def test_page_display_name(url, expected):
    assert page_display_name(url) == expected


def test_measured_length_only_for_title_and_meta_description():
    page = SimpleNamespace(title="  Hello world  ", meta_description="abc", canonical="https://x")
    assert measured_length(page, "title") == 11
    assert measured_length(page, "meta_description") == 3
    assert measured_length(page, "canonical") is None
    assert measured_length(page, "h1") is None


def test_measured_length_is_none_for_blank_or_missing_page():
    assert measured_length(SimpleNamespace(title="   ", meta_description=None), "title") is None
    assert measured_length(SimpleNamespace(title="x", meta_description=None), "meta_description") is None
    assert measured_length(None, "title") is None
