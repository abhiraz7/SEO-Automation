"""
Picks Claude or Gemini per the /settings AI Provider toggle. Reuses
ProviderSetting (same table already used for the on-page DataForSEO/SEMrush
toggle in routes/settings.py) -- exactly one of AI_PROVIDERS is enabled at
a time. Both app/claude.py and app/gemini.py expose an identical public
interface (complete/generate_suggestions/generate_meta_optimization), so
this module just picks which one to call -- callers use this module
instead of importing claude/gemini directly.
"""
from sqlalchemy.orm import Session

from . import claude, gemini, models

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
