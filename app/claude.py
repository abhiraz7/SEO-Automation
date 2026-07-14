"""
Thin Claude API client. All prompt construction lives in app/prompt_builder.py —
this module only sends prompts and parses responses.
"""
import os
import anthropic

from . import prompt_builder

MODEL = "claude-haiku-4-5-20251001"

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def _complete(prompt: str, max_tokens: int, temperature: float = 1.0, model: str = MODEL) -> str:
    message = _get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()


def complete(prompt: str, max_tokens: int, temperature: float = 1.0, model: str = MODEL) -> str:
    """Public entry point for callers outside this module (e.g. app/services/*)
    that need direct control over temperature/model rather than one of the
    higher-level generate_* helpers below."""
    return _complete(prompt, max_tokens=max_tokens, temperature=temperature, model=model)


def generate_suggestions(context: dict) -> list[str]:
    """Ready-to-use replacement values for an audit issue (count set by
    prompt_builder.SUGGESTION_COUNT). Context comes from
    prompt_builder.build_context(page, issue, business_profile)."""
    raw = _complete(prompt_builder.build_suggestion_prompt(context), max_tokens=1024, temperature=0.7)
    prefixes = [f"{n}." for n in range(1, prompt_builder.SUGGESTION_COUNT + 1)]
    suggestions = []
    for line in (l.strip() for l in raw.splitlines() if l.strip()):
        for prefix in prefixes:
            if line.startswith(prefix):
                suggestions.append(line[len(prefix):].strip())
                break
    return suggestions[:prompt_builder.SUGGESTION_COUNT]


def generate_meta_optimization(context: dict) -> dict:
    """Optimized meta title + description for a page."""
    raw = _complete(prompt_builder.build_meta_optimization_prompt(context), max_tokens=256)
    result = {"title": None, "description": None}
    for line in raw.splitlines():
        line = line.strip()
        if line.lower().startswith("title:"):
            result["title"] = line.split(":", 1)[1].strip()
        elif line.lower().startswith("description:"):
            result["description"] = line.split(":", 1)[1].strip()
    return result
