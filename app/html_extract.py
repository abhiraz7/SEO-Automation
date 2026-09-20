"""
Shared <img> extraction -- used by both app/crawler.py's own-crawl pipeline
and app/dataforseo_onpage.py's supplementary fetch_image_alts() (the
DataForSEO-sourced on-page view has no per-image field of its own, see that
module's docstring). One place for the src-resolution logic instead of two
copies silently drifting apart.
"""
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

# WordPress's editor (block or classic) stamps every image it inserts with
# a "wp-image-{attachment ID}" CSS class -- e.g. class="attachment-full
# size-full wp-image-4842". That ID is exactly the media_id our WordPress
# plugin's update_media_meta tool needs to fix alt text, and it's sitting
# right there in HTML we already fetch -- no extra request. It only covers
# editor-inserted content images though: theme-level images (logo, header/
# footer, anything set via the Customizer) are rendered by the theme's own
# template code and carry no such class, so media_id stays None for those --
# those need a separate URL->ID lookup (attachment_url_to_postid) at deploy
# time instead.
_WP_IMAGE_CLASS = re.compile(r"wp-image-(\d+)")


def _wp_media_id(img) -> int | None:
    for cls in img.get("class") or []:
        m = _WP_IMAGE_CLASS.fullmatch(cls)
        if m:
            return int(m.group(1))
    return None


def extract_image_alts(soup: BeautifulSoup, base_url: str) -> list[dict]:
    """Every <img> tag's src (resolved to an absolute URL against base_url),
    alt text, and WordPress media_id when the markup reveals one (see
    _wp_media_id). A relative src ("/wp-content/x.jpg", "images/x.jpg") is
    meaningless outside the page it came from -- resolved here once so
    nothing downstream (a rendered <img> thumbnail, a stored report) has to
    re-derive it. urljoin already leaves an already-absolute src unchanged
    and correctly resolves a protocol-relative one ("//cdn.example.com/x.jpg")
    against base_url's scheme. A missing/empty src stays None -- nothing to
    resolve."""
    images = []
    for img in soup.find_all("img"):
        src = img.get("src")
        resolved = urljoin(base_url, src) if src else None
        images.append({"src": resolved, "alt": img.get("alt"), "media_id": _wp_media_id(img)})
    return images
