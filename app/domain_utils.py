"""
Shared domain normalization -- used by the Competitor module so
example.com, http://example.com, https://www.example.com/, and
WWW.EXAMPLE.COM/ all resolve to the same stored value and the same
uniqueness check. Deliberately separate from semrush.py/dataforseo.py's
own _domain_only helpers (which strip protocol + path but not "www.",
and aren't used for uniqueness) -- this one exists specifically to make
"is this the same competitor" a reliable comparison, not just a per-call
API target string.
"""


def normalize_domain(value: str) -> str:
    """example.com -- lowercase, no scheme, no "www.", no trailing slash,
    no path/query/fragment. Empty string in, empty string out (caller
    decides whether that's a validation error)."""
    value = (value or "").strip().lower()
    if not value:
        return ""
    value = value.replace("https://", "").replace("http://", "")
    value = value.split("/")[0].split("?")[0].split("#")[0]
    if value.startswith("www."):
        value = value[4:]
    return value.strip().rstrip(".")
