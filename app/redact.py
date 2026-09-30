"""
Secret redaction for anything that is stored or shown as a log.

The Settings page shows API error logs raw, to anyone who can open it (the app
has no login). A raw log can still carry a credential by accident: SEMrush takes
its key as a URL query parameter, so an HTTP error message can contain the whole
URL, and an SDK error can echo a header. Everything is passed through redact()
BEFORE it is stored, so the database never holds a secret and the page can't
show one.

Three layers, cheapest first:
1. the live VALUES of every environment variable whose name looks like a secret
   (KEY / TOKEN / SECRET / PASSWORD) -- catches the exact keys this server uses,
   whatever their format;
2. well-known key shapes (Anthropic/OpenAI style sk-..., Google AIza..., Bearer /
   Basic auth values);
3. `name=value` pairs whose name looks like a secret (key=, token=, password= ...).
"""
import os
import re

_SECRET_ENV_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD)", re.IGNORECASE)
_MIN_SECRET_LEN = 8   # never replace very short values: it would mangle ordinary text

_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), "[redacted:anthropic-key]"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "[redacted:api-key]"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), "[redacted:google-key]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-~+/=]{8,}"), "Bearer [redacted]"),
    (re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=]{8,}"), "Basic [redacted]"),
    (re.compile(r"(?i)\b((?:[a-z_]*)(?:api[_-]?key|apikey|key|token|secret|password|passwd|authorization))=([^&\s\"']+)"),
     r"\1=[redacted]"),
]


def _secret_values(environ) -> list[tuple[str, str]]:
    found = []
    for name, value in environ.items():
        if value and len(value) >= _MIN_SECRET_LEN and _SECRET_ENV_NAME.search(name):
            found.append((name, value))
    found.sort(key=lambda nv: len(nv[1]), reverse=True)   # longest first, so a value that contains another is replaced whole
    return found


def redact(text, environ=None) -> str:
    """text -> text with secrets replaced by [redacted...]. Safe on None/non-str."""
    if text is None:
        return ""
    out = str(text)
    for name, value in _secret_values(os.environ if environ is None else environ):
        out = out.replace(value, f"[redacted:{name}]")
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out
