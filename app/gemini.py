"""
Thin Gemini API client. All prompt construction lives in app/prompt_builder.py --
this module only sends prompts and parses responses. Mirrors app/claude.py's
public interface exactly so callers can swap providers by changing one import.
"""
import os
from google import genai
from google.genai import types

from . import prompt_builder

MODEL = "gemini-flash-latest"

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ["GEMENI_KEY"])
    return _client


def _complete(prompt: str, max_tokens: int, temperature: float = 1.0, model: str = MODEL) -> str:
    response = _get_client().models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
            # Our prompts want direct structured output (suggestions, meta
            # tags), not open-ended reasoning -- thinking tokens would
            # otherwise silently eat the whole max_output_tokens budget
            # before any visible text is produced (seen live: MAX_TOKENS
            # finish_reason with empty content on the default settings).
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return response.text.strip()


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
