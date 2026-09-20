"""
Picks Claude or Gemini per the /settings AI Provider toggle. Reuses
ProviderSetting (same table already used for the on-page DataForSEO/SEMrush
toggle in routes/settings.py) -- exactly one of AI_PROVIDERS is enabled at
a time. Both app/claude.py and app/gemini.py expose an identical public
interface (complete/generate_suggestions/generate_meta_optimization), so
this module just picks which one to call -- callers use this module
instead of importing claude/gemini directly.
"""
import json

from pydantic import ValidationError
from sqlalchemy.orm import Session

from . import claude, gemini, models, prompt_builder
from .ai_errors import AIGenerationError, ImageFetchError
from .schemas import ImageAltSuggestionsResult
from .services import image_fetch

AI_PROVIDERS = ("claude", "gemini")


def get_active_ai_provider(db: Session) -> str:
    row = (
        db.query(models.ProviderSetting)
        .filter(models.ProviderSetting.provider.in_(AI_PROVIDERS), models.ProviderSetting.enabled.is_(True))
        .first()
    )
    return row.provider if row else "claude"


def _module(db: Session):
    return gemini if get_active_ai_provider(db) == "gemini" else claude


def complete(db: Session, prompt: str, max_tokens: int, temperature: float = 1.0) -> str:
    """No model= param here -- each provider module owns its own default
    MODEL constant. A Claude-specific model string passed to Gemini (or
    vice versa) would just error, so callers that used to pass model=
    explicitly (context_builder.py) now let the active module decide."""
    return _module(db).complete(prompt, max_tokens=max_tokens, temperature=temperature)


def generate_suggestions(db: Session, context: dict) -> list[str]:
    return _module(db).generate_suggestions(context)


def generate_meta_optimization(db: Session, context: dict) -> dict:
    return _module(db).generate_meta_optimization(context)


def _parse_image_alt_json(raw: str) -> ImageAltSuggestionsResult:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    data = json.loads(text)
    return ImageAltSuggestionsResult(**data)


def generate_image_alt_suggestions(db: Session, context: dict, image_src: str) -> list[dict]:
    """Image Alt AI hardening pass, Part 1/7/8: fetches the actual image,
    sends it (not just its URL/filename) to whichever provider is active via
    that provider's own image_alt_completion(), then centrally parses +
    validates + retries the JSON response -- this is the ONE place that
    logic lives, so claude.py and gemini.py stay pure API-shape adapters with
    no duplicated SEO/parsing logic between them (Part 8: provider parity).

    Raises ImageFetchError if the image can't be fetched/isn't a supported
    type -- deliberately not caught here and not silently downgraded to a
    text-only guess, per Part 1's "do not pretend visual analysis happened".
    Raises AIGenerationError if the provider's response still doesn't parse
    as valid JSON after one retry, instead of returning an empty list that
    would look identical to "the model just had nothing to suggest"."""
    fetched = image_fetch.fetch_image_bytes(image_src)
    if fetched is None:
        raise ImageFetchError(f"Could not fetch image for AI visual analysis: {image_src!r}")
    image_bytes, media_type = fetched

    module = _module(db)
    user_text = prompt_builder.build_image_alt_user_text(context)

    raw = module.image_alt_completion(user_text, image_bytes, media_type)
    try:
        result = _parse_image_alt_json(raw)
    except (json.JSONDecodeError, ValidationError, TypeError):
        raw = module.image_alt_completion(user_text, image_bytes, media_type)
        try:
            result = _parse_image_alt_json(raw)
        except (json.JSONDecodeError, ValidationError, TypeError) as e:
            raise AIGenerationError(f"AI provider returned malformed image_alt JSON after retry: {e}") from e

    return [s.model_dump() for s in result.suggestions]
