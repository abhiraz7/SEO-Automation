"""
Which API keys / secrets this server has, for the Settings page.

Shows STATUS ONLY -- set or missing, how long, and the last four characters of
long values so you can tell two keys apart. It never returns or renders a value.
The Settings page has no login and the prod URL is reachable by anyone, so a page
that displayed raw keys would hand them to whoever loads it. To read a key,
open the server's environment; to check that one WORKS, use the error log below
it, which records what each provider actually answered.
"""
import os

# (environment variable, what it is for)
KEYS = [
    ("ANTHROPIC_API_KEY", "Claude: AI suggestions, page understanding, content optimizer"),
    ("GEMENI_KEY", "Gemini: alternative AI provider (note: spelled GEMENI in the code)"),
    ("DATAFORSEO_LOGIN", "DataForSEO login: on-page audits, keywords, SERP, backlinks, LLM mentions"),
    ("DATAFORSEO_PASSWORD", "DataForSEO password"),
    ("SEMRUSH_API_KEY", "SEMrush: keywords, backlinks, on-page audit (when that provider is active)"),
    ("WP_TOKEN_KEY", "Encrypts the stored WordPress connection tokens (a Fernet key)"),
    ("GOOGLE_OAUTH_CLIENT_ID", "Google Search Console: OAuth client id"),
    ("GOOGLE_OAUTH_CLIENT_SECRET", "Google Search Console: OAuth client secret"),
    ("GOOGLE_OAUTH_REDIRECT_URI", "Google Search Console: OAuth redirect URI"),
    ("GSC_TOKEN_KEY", "Encrypts the stored Google tokens (a Fernet key)"),
    ("SUPABASE_URL", "Supabase project URL (optional)"),
    ("SUPABASE_KEY", "Supabase key (optional)"),
]

_TAIL_MIN_LENGTH = 12   # below this, even four characters is too much of the value


def mask(value: str) -> str:
    """'' for unset; otherwise only a length, plus the last 4 characters when the
    value is long enough for that to be a small fraction of it."""
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) < _TAIL_MIN_LENGTH:
        return f"set ({len(value)} characters)"
    return f"set, ends …{value[-4:]} ({len(value)} characters)"


def key_status(environ=None) -> list[dict]:
    env = os.environ if environ is None else environ
    out = []
    for name, purpose in KEYS:
        shown = mask(env.get(name, ""))
        out.append({"name": name, "purpose": purpose, "is_set": bool(shown), "masked": shown})
    return out
