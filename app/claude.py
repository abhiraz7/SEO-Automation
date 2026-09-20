"""
Thin Claude API client. All prompt construction lives in app/prompt_builder.py —
this module only sends prompts and parses responses.
"""
import base64
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


def image_alt_completion(user_text: str, image_bytes: bytes, media_type: str, max_tokens: int = 1024, temperature: float = 0.4) -> str:
    """Provider-specific half of the image_alt pipeline (Part 1/8) -- sends
    the actual image bytes as a vision content block, plus user_text (built
    by prompt_builder.build_image_alt_user_text) as a second content block in
    the same user turn. IMAGE_ALT_TASK_RULES goes in the real `system`
    parameter, not the user turn -- the one part of this call untrusted page/
    user content can never edit merely by looking like an instruction. All
    SEO/prompt logic lives in prompt_builder.py; this function only knows
    Anthropic's message/content-block shape. JSON parsing + schema validation
    + retry live in ai_provider.py, shared with gemini.py's identical
    counterpart -- see that module's generate_image_alt_suggestions."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    message = _get_client().messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        temperature=temperature,
        system=prompt_builder.IMAGE_ALT_TASK_RULES,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                {"type": "text", "text": user_text},
            ],
        }],
    )
    return message.content[0].text.strip()


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
