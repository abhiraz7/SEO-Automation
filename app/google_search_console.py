"""
Google Search Console adapter -- OAuth + the four Search Console APIs
(Sites, Search Analytics, URL Inspection, Sitemaps), all through one
`searchconsole` v1 service object. Mirrors semrush.py/dataforseo.py's role
for keywords and wordpress.py's role for deploy: nothing outside this file
should know Google's request/response shapes.

Every public function returns an explicit ok/no_data/error result -- same
three-outcome discipline as the other providers. GSC is never a hard
dependency: a project with no connection, or a call that fails, must not
break any existing feature (deploy, suggestions, competitor gap, optimizer).

Connection model (see prompts/CLAUDE_FEATURE_3_GOOGLE_SEARCH_CONSOLE.md,
"Confirmed decisions", 2026-09-29): strictly one google_connections row per
project, never shared/reused across projects. Whichever Google account
already has Search Console access to a project's property is the one that
completes OAuth for that project -- the code here doesn't care whose
account it is, only that the account holds at least Full user access.

BLOCKED IN THIS ENVIRONMENT (same caveat style as wordpress.py): real
end-to-end OAuth needs a Google Cloud OAuth client (Task 0 -- Client ID/
Secret, both redirect URIs registered, both test accounts added) that is not
finished as of this module's first version. Functions here are implemented
and unit-testable (mocked google-api-python-client calls) but not yet
verified against a live Google account.
"""
import os

_ENV_CLIENT_ID = "GOOGLE_OAUTH_CLIENT_ID"
_ENV_CLIENT_SECRET = "GOOGLE_OAUTH_CLIENT_SECRET"
_ENV_REDIRECT_URI = "GOOGLE_OAUTH_REDIRECT_URI"
_ENV_TOKEN_KEY = "GSC_TOKEN_KEY"

_REQUIRED_ENV_VARS = (_ENV_CLIENT_ID, _ENV_CLIENT_SECRET, _ENV_REDIRECT_URI, _ENV_TOKEN_KEY)

# Read-only scope only, per the staged-scope OAuth plan (design doc §17):
# covers every read this feature does (Sites list, Search Analytics query,
# Sitemaps list/get, URL Inspection). The write scope
# ("https://www.googleapis.com/auth/webmasters") is added later, only if/
# when sitemap-submit (Phase 6) actually ships.
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]


def is_configured() -> bool:
    """True only if every required GSC env var is set to a non-blank value.
    Callers (routes, UI) use this to show "GSC not configured" instead of
    letting a missing var surface as a crash deeper in the OAuth flow --
    same degrade-gracefully contract Task 1's verify step requires."""
    return all(os.environ.get(name, "").strip() for name in _REQUIRED_ENV_VARS)
