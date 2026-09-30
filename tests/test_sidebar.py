"""
Sidebar contract: every item is a real page (nothing greyed out / "coming
soon"), exactly one item is highlighted, and the project block appears only
inside a project. Renders the partial directly with a fake request path.
"""
import re
from types import SimpleNamespace

import pytest
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")
ACTIVE = "color:#3b3fce"


def render(path, project=None):
    ctx = {"request": SimpleNamespace(url=SimpleNamespace(path=path))}
    if project is not None:
        ctx["project"] = project
    return templates.env.get_template("partials/sidebar.html").render(**ctx)


def active_labels(html):
    """Text of every highlighted link."""
    out = []
    for m in re.finditer(r'<a href="([^"]*)" style="[^"]*' + re.escape(ACTIVE) + r'[^"]*"[^>]*>(.*?)</a>', html, re.S):
        out.append(re.sub(r"<[^>]+>", "", m.group(2)).strip())
    return out


PATHS = [
    "/", "/keywords", "/keywords/3", "/visibility", "/security", "/settings",
    "/projects/9", "/projects/9/onpage", "/projects/9/pages/5", "/projects/9/visibility",
    "/projects/9/competitors", "/projects/9/optimizer", "/projects/9/optimizer/runs/2", "/projects/9/links",
    "/projects/9/security",
]


@pytest.mark.parametrize("path", PATHS)
def test_nothing_is_greyed_out_or_dead(path):
    html = render(path)
    assert "cursor:not-allowed" not in html and "coming soon" not in html.lower()
    for dead in ("Schema Generator", "AI Writer", "Reports"):
        assert dead not in html, dead
    assert 'href="#"' not in html.replace('href="#" onclick', "")   # only the Queue drawer trigger uses '#'


@pytest.mark.parametrize("path", PATHS)
def test_at_most_one_item_is_highlighted(path):
    assert len(active_labels(render(path))) <= 1


@pytest.mark.parametrize("path,expected", [
    ("/", "Projects"), ("/keywords", "Keyword Research"), ("/keywords/3", "Keyword Research"),
    ("/visibility", "AI Visibility"), ("/security", "Security Testing"), ("/settings", "Settings"),
    ("/projects/9", "On-page SEO"), ("/projects/9/onpage", "On-page SEO"), ("/projects/9/pages/5", "On-page SEO"),
    ("/projects/9/visibility", "AI Visibility"), ("/projects/9/competitors", "Competitors"),
    ("/projects/9/optimizer", "Content Optimizer"), ("/projects/9/optimizer/runs/2", "Content Optimizer"),
    ("/projects/9/links", "Link Analyzer"), ("/projects/9/security", "Security check"),
])
def test_the_right_item_is_highlighted(path, expected):
    assert active_labels(render(path)) == [expected]


def test_projects_is_not_highlighted_inside_a_project():
    # the old bug: "Projects" stayed lit on every /projects/... page
    assert "Projects" not in active_labels(render("/projects/9/links"))


def test_project_block_only_inside_a_project():
    inside = render("/projects/9/onpage", project=SimpleNamespace(name="Acme Notes"))
    assert "Acme Notes" in inside and "All projects" in inside
    for label in ("On-page SEO", "Backlinks", "Competitors", "Content Optimizer", "Link Analyzer", "Queue"):
        assert label in inside, label
    outside = render("/")
    assert "All projects" not in outside and "Queue" not in outside and "Content Optimizer" not in outside


def test_project_name_falls_back_to_the_id_when_the_route_passes_no_project():
    assert "Project 9" in render("/projects/9/visibility")


def test_project_links_point_at_that_project():
    html = render("/projects/42/onpage", project=SimpleNamespace(name="X"))
    for suffix in ("/optimizer", "/competitors", "/links", "/visibility", "/security", "/keywords", "#backlinks"):
        assert f'href="/projects/42{suffix}"' in html, suffix


def test_non_numeric_project_path_is_treated_as_global():
    assert "All projects" not in render("/projects/new")
