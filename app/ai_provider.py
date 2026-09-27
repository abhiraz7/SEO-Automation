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
from .services import action_plan, image_fetch

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


# ── AI Competitor Gap -> SEO Action Plan ─────────────────────────────────────
# Same pattern as generate_image_alt_suggestions: this module owns the call, the JSON
# parsing and ONE retry; claude.py / gemini.py stay pure API adapters (via complete()).
# A response that cannot be used raises AIGenerationError instead of becoming an empty
# plan, so "the model produced nothing usable" is never confused with "no gaps".

PLAN_MAX_TOKENS = 3000
DRAFT_MAX_TOKENS = 1500


def _call_model(db: Session, prompt: str, max_tokens: int, temperature: float) -> str:
    try:
        return complete(db, prompt, max_tokens=max_tokens, temperature=temperature)
    except Exception as exc:  # noqa: BLE001 -- any provider failure is surfaced, never swallowed
        raise AIGenerationError(f"AI provider call failed: {exc}") from exc


def generate_action_plan(db: Session, bundle: dict, business_profile=None) -> dict:
    """The validated action plan for one analysis: {"status", "actions", "rejected",
    "warnings"} (see services/action_plan.validate_action_plan). `bundle["evidence"]`
    is the numbered evidence list the model may cite.

    Raises AIGenerationError if the provider fails, or if its answer is still not
    valid JSON for the schema after one retry. An answer that parses but whose actions
    are all rejected (unknown evidence, forbidden claims, ...) is NOT an error: it comes
    back as status 'no_data' with the reasons in `rejected`."""
    prompt = prompt_builder.build_action_plan_prompt(bundle, business_profile)
    last_error = None
    for _attempt in (1, 2):
        raw = _call_model(db, prompt, PLAN_MAX_TOKENS, temperature=0.3)
        try:
            parsed = action_plan.parse_action_plan(raw)
        except action_plan.PlanFormatError as exc:
            last_error = exc
            continue
        return action_plan.validate_action_plan(parsed, bundle.get("evidence") or [])
    raise AIGenerationError(f"AI provider returned an unusable action plan after retry: {last_error}")


def generate_gap_draft(db: Session, bundle: dict, action: dict, competitor_texts: list[str], business_profile=None) -> dict:
    """One atomic, validated draft for one action: {"draft", "claims_to_verify",
    "warnings"}. A draft that fails validation (thin, oversized, forbidden claim, or a
    run of competitor wording) gets ONE retry with the reason fed back; still failing
    raises AIGenerationError with that reason. Never returns an unvalidated draft."""
    correction = None
    last_error = None
    for _attempt in (1, 2):
        prompt = prompt_builder.build_gap_draft_prompt(bundle, action, business_profile, correction=correction)
        raw = _call_model(db, prompt, DRAFT_MAX_TOKENS, temperature=0.5)
        try:
            parsed = action_plan.parse_gap_draft(raw)
        except action_plan.PlanFormatError as exc:
            last_error = str(exc)
            correction = "it was not valid JSON in the required shape"
            continue
        checked = action_plan.validate_draft(parsed, competitor_texts)
        if checked["ok"]:
            return {"draft": checked["draft"], "claims_to_verify": checked["claims_to_verify"], "warnings": checked["warnings"]}
        last_error = checked["error"]
        correction = checked["error"]
    raise AIGenerationError(f"AI provider could not produce an acceptable draft after retry: {last_error}")
