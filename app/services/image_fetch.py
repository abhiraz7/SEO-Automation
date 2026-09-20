"""
Fetches raw image bytes for the image_alt AI suggestion pipeline (Part 1 of
the image-alt hardening pass) so Claude/Gemini can actually SEE the image
instead of guessing from its URL/filename -- see prompt_builder.py's
IMAGE_ALT_TASK_RULES and ai_provider.generate_image_alt_suggestions.

Deliberately separate from dataforseo_onpage.fetch_image_alts, which parses
every <img> tag out of a page's HTML to build the missing-alt list in the
first place -- this fetches the BYTES of one already-known image, for one
AI call, not a page's whole image inventory.
"""
import httpx

_TIMEOUT = 15
# Generous for a real web image, small enough that one "Generate" click on a
# giant asset can't stall the request indefinitely or blow past what the
# provider APIs accept inline.
_MAX_BYTES = 8 * 1024 * 1024
# Anthropic and Gemini both accept these four inline; anything else (svg,
# avif, bmp, ...) is rejected here rather than sent and failing provider-side
# with a less legible error.
_ALLOWED_MEDIA_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def fetch_image_bytes(url: str | None) -> tuple[bytes, str] | None:
    """(image_bytes, media_type) for a fetchable, provider-supported image,
    or None on ANY failure (bad URL, network error, non-2xx, unsupported or
    missing content-type, over the size cap) -- never raises. The caller
    (ai_provider.generate_image_alt_suggestions) turns a None into a
    controlled ImageFetchError instead of proceeding as if visual analysis
    happened -- see app/ai_errors.py."""
    if not url:
        return None
    try:
        with httpx.stream("GET", url, timeout=_TIMEOUT, follow_redirects=True) as resp:
            resp.raise_for_status()
            media_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            if media_type not in _ALLOWED_MEDIA_TYPES:
                return None
            chunks = []
            total = 0
            for chunk in resp.iter_bytes():
                total += len(chunk)
                if total > _MAX_BYTES:
                    return None
                chunks.append(chunk)
            if total == 0:
                return None
            return b"".join(chunks), media_type
    except Exception:
        return None
