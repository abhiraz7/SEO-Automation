"""
FIELD_DEPLOYERS registry tests (Task 3.5). Confirms each wired category
(meta_description/title/h1) calls the right plugin tool with the right
params and reads the right key back for before_value. wordpress.py's own
HTTP-shape tests live in test_wordpress.py; these test the routing layer
on top of it -- no network, no DB.
"""
from unittest.mock import MagicMock, patch

from app import wordpress
from app.routes import wordpress as wp_routes


def _ok_result(data):
    return wordpress.WordPressResult(status="ok", data=data)


def test_meta_description_deployer_calls_yoast_set_meta_with_correct_kwarg():
    deployer = wp_routes.FIELD_DEPLOYERS["meta_description"]
    with patch.object(wordpress, "set_yoast_meta", return_value=_ok_result({})) as mock_write:
        deployer["write"]("https://site.com", "tok", 7, "new description")
    mock_write.assert_called_once_with("https://site.com", "tok", 7, meta_description="new description")


def test_title_deployer_writes_seo_title_not_post_title():
    """The critical distinction this task's docstring calls out: 'title'
    issues are the Yoast SEO <title> tag, not the WordPress post_title."""
    deployer = wp_routes.FIELD_DEPLOYERS["title"]
    with patch.object(wordpress, "set_yoast_meta", return_value=_ok_result({})) as mock_write:
        deployer["write"]("https://site.com", "tok", 7, "New SEO Title")
    mock_write.assert_called_once_with("https://site.com", "tok", 7, seo_title="New SEO Title")


def test_h1_deployer_writes_post_title_via_update_post():
    deployer = wp_routes.FIELD_DEPLOYERS["h1"]
    with patch.object(wordpress, "update_post_content", return_value=_ok_result({})) as mock_write:
        deployer["write"]("https://site.com", "tok", 7, "New H1")
    mock_write.assert_called_once_with("https://site.com", "tok", 7, title="New H1")


def test_read_key_extracts_before_value_from_read_result():
    for category, read_fn_name, read_key, response_key in [
        ("meta_description", "get_yoast_meta", "meta_description", "meta_description"),
        ("title", "get_yoast_meta", "seo_title", "seo_title"),
        ("h1", "get_post", "title", "title"),
    ]:
        deployer = wp_routes.FIELD_DEPLOYERS[category]
        assert deployer["read_key"] == read_key
        with patch.object(wordpress, read_fn_name, return_value=_ok_result({response_key: "current value"})):
            result = deployer["read"]("https://site.com", "tok", 7)
        assert result.data[deployer["read_key"]] == "current value"


def test_image_alt_is_not_in_field_deployers():
    """Documented gap: deploying alt text needs a media_id, which the
    current DeployIn(wp_post_id) contract can't carry. Must stay absent
    until that's resolved, not silently mis-wired to a post_id."""
    assert "image_alt" not in wp_routes.FIELD_DEPLOYERS
