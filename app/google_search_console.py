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
Secret, both redirect URIs registered, both test accounts added). Functions
here are implemented and unit-testable (mocked google-auth-oauthlib /
google-api-python-client calls) but NOT yet verified against a live Google
account -- that verification happens once Task 0 is done and Task 4's
routes exist to drive a real browser click-through.
"""
import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .services import failure_log

_ENV_CLIENT_ID = "GOOGLE_OAUTH_CLIENT_ID"
_ENV_CLIENT_SECRET = "GOOGLE_OAUTH_CLIENT_SECRET"
_ENV_REDIRECT_URI = "GOOGLE_OAUTH_REDIRECT_URI"
_ENV_TOKEN_KEY = "GSC_TOKEN_KEY"

_REQUIRED_ENV_VARS = (_ENV_CLIENT_ID, _ENV_CLIENT_SECRET, _ENV_REDIRECT_URI, _ENV_TOKEN_KEY)

_TOKEN_URI = "https://oauth2.googleapis.com/token"
_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"

# Read-only Search Console scope, per the staged-scope OAuth plan (design
# doc §17): covers every read this feature does (Sites list, Search
# Analytics query, Sitemaps list/get, URL Inspection). The write scope
# ("https://www.googleapis.com/auth/webmasters") is added later, only if/
# when sitemap-submit (Phase 6) actually ships. openid + userinfo.email are
# added alongside it (both non-sensitive scopes) purely to identify WHICH
# Google account completed the connect -- google_connections.
# google_account_email needs a real value, and Search Console's own API has
# no "who am I" endpoint of its own.
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/webmasters.readonly",
]

_SEARCHCONSOLE_SERVICE = "searchconsole"
_SEARCHCONSOLE_VERSION = "v1"


def is_configured() -> bool:
    """True only if every required GSC env var is set to a non-blank value.
    Callers (routes, UI) use this to show "GSC not configured" instead of
    letting a missing var surface as a crash deeper in the OAuth flow --
    same degrade-gracefully contract Task 1's verify step requires."""
    return all(os.environ.get(name, "").strip() for name in _REQUIRED_ENV_VARS)


def _get_fernet() -> Fernet:
    """GSC_TOKEN_KEY must be a Fernet key (44-char urlsafe-base64 string).
    Mirrors app/wordpress.py's _get_fernet() exactly, pointed at a SEPARATE
    key -- GSC and WordPress tokens must not share one, so a compromise of
    one store doesn't expose the other. Generate one with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    and put it in .env."""
    key = os.environ.get(_ENV_TOKEN_KEY, "").strip()
    if not key:
        raise RuntimeError(
            f"{_ENV_TOKEN_KEY} is not set. Generate one with: "
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" '
            "and add it to .env."
        )
    return Fernet(key.encode())


def encrypt_token(raw_token: str) -> str:
    return _get_fernet().encrypt(raw_token.encode()).decode()


def decrypt_token(encrypted_token: str) -> str:
    try:
        return _get_fernet().decrypt(encrypted_token.encode()).decode()
    except InvalidToken as e:
        raise RuntimeError("Stored Google token could not be decrypted -- GSC_TOKEN_KEY may have changed.") from e


@dataclass
class GSCResult:
    """Three-outcome contract, same shape/reasoning as WordPressResult:
    ok/no_data/error must stay distinguishable all the way to the UI."""
    status: str  # "ok" | "no_data" | "error"
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _client_config() -> dict:
    return {
        "web": {
            "client_id": os.environ[_ENV_CLIENT_ID],
            "client_secret": os.environ[_ENV_CLIENT_SECRET],
            "auth_uri": _AUTH_URI,
            "token_uri": _TOKEN_URI,
            "redirect_uris": [os.environ[_ENV_REDIRECT_URI]],
        }
    }


_STATE_MAX_AGE_SECONDS = 600  # 10 minutes -- generous for a human to click through Google's consent screen, tight enough that a captured/replayed state link goes stale fast


def sign_state(project_id: int) -> str:
    """Builds the tamper-proof `state` value Task 4's /gsc/connect passes to
    build_auth_url() and /gsc/callback verifies. The app has no session/
    login system to keep state server-side (see build_auth_url's docstring),
    so the state string itself must carry project_id AND prove it wasn't
    forged or reused from elsewhere. Reuses the same GSC_TOKEN_KEY Fernet
    key already justified for token-at-rest encryption -- Fernet gives both
    tamper-detection (an HMAC under the hood) and a built-in TTL via
    decrypt(ttl=...), so no extra signing library (e.g. itsdangerous) is
    needed for this."""
    payload = json.dumps({"project_id": project_id, "nonce": secrets.token_urlsafe(8)})
    return _get_fernet().encrypt(payload.encode()).decode()


def verify_state(state: str) -> int | None:
    """Returns the project_id embedded in a state string sign_state()
    produced, or None if it's missing, forged, or older than
    _STATE_MAX_AGE_SECONDS. Task 4's /gsc/callback must reject the request
    (400) on None rather than guessing a project_id from anywhere else."""
    try:
        payload = _get_fernet().decrypt(state.encode(), ttl=_STATE_MAX_AGE_SECONDS)
    except Exception:
        return None
    try:
        data = json.loads(payload.decode())
        return int(data["project_id"])
    except (ValueError, KeyError, TypeError):
        return None


def build_auth_url(state: str) -> str:
    """Builds the URL to redirect the browser to for Google's consent
    screen. `state` must already be a signed, tamper-proof token carrying
    whatever the callback needs to know (at minimum project_id) -- this
    function does not sign or validate it, only passes it through, since
    the app has no session/login system to keep it in server-side.

    access_type=offline + prompt=consent are both required to reliably get
    a refresh_token back: Google only issues one on a user's FIRST consent
    for an app otherwise, so without prompt=consent a returning user who
    reconnects (e.g. after the 7-day Testing-mode expiry) would silently
    get no refresh_token at all.

    A fresh Flow is built here and discarded -- per Task 3's design, the
    Flow object is never kept alive between requests, only the opaque
    `state` string round-trips through the redirect."""
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES, state=state)
    flow.redirect_uri = os.environ[_ENV_REDIRECT_URI]
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )
    return auth_url


def _account_email(creds: Credentials) -> str | None:
    """The connected account's email, via the (non-sensitive) userinfo.email
    scope granted alongside webmasters.readonly -- Search Console's own API
    has no equivalent of "who am I"."""
    try:
        service = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        info = service.userinfo().get().execute()
    except Exception:
        return None
    return info.get("email")


def exchange_code_for_tokens(code: str) -> GSCResult:
    """Exchanges the callback's authorization `code` for real tokens. Called
    fresh in /gsc/callback (Task 4) -- a new Flow, not one kept from
    /gsc/connect, per the same "don't keep Flow alive between requests"
    rule build_auth_url follows."""
    try:
        flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
        flow.redirect_uri = os.environ[_ENV_REDIRECT_URI]
        flow.fetch_token(code=code)
    except Exception as e:
        failure_log.failure("google_search_console.exchange_code_for_tokens", str(e))
        return GSCResult(status="error", error=f"Could not exchange authorization code: {e}")

    creds = flow.credentials
    if not creds.refresh_token:
        # Should not happen given access_type=offline + prompt=consent on
        # the auth URL, but if it does, storing a connection with no
        # refresh_token is worse than refusing it -- it would silently die
        # the moment the short-lived access token expires.
        failure_log.failure("google_search_console.exchange_code_for_tokens", "no refresh_token in response")
        return GSCResult(status="error", error="Google did not return a refresh token -- reconnect and make sure to approve consent.")

    email = _account_email(creds)
    if not email:
        failure_log.failure("google_search_console.exchange_code_for_tokens", "could not resolve account email")
        return GSCResult(status="error", error="Connected, but could not determine the Google account's email.")

    return GSCResult(status="ok", data={
        "google_account_email": email,
        "access_token": creds.token,
        "refresh_token": creds.refresh_token,
        "expiry": creds.expiry,
        "scope": " ".join(creds.scopes or SCOPES),
    })


def refresh_access_token(refresh_token: str) -> GSCResult:
    """Explicit refresh -- used by Phase 3's scheduled snapshot job and
    anywhere else that needs a fresh access token before it has one to try
    first. invalid_grant means the connection is revoked (user removed
    access, or the account was dropped as a GSC user) -- data['revoked'] is
    True in that case so the caller stores status='revoked' rather than
    retrying, which would just fail forever."""
    creds = Credentials(
        None,
        refresh_token=refresh_token,
        client_id=os.environ[_ENV_CLIENT_ID],
        client_secret=os.environ[_ENV_CLIENT_SECRET],
        token_uri=_TOKEN_URI,
        scopes=SCOPES,
    )
    try:
        creds.refresh(Request())
    except RefreshError as e:
        revoked = "invalid_grant" in str(e)
        failure_log.failure("google_search_console.refresh_access_token", str(e), revoked=revoked)
        return GSCResult(status="error", error=str(e), data={"revoked": revoked})
    except Exception as e:
        failure_log.crash("google_search_console.refresh_access_token", e)
        return GSCResult(status="error", error=f"Could not refresh access token: {e}")

    return GSCResult(status="ok", data={"access_token": creds.token, "expiry": creds.expiry})


def _build_service(access_token: str, refresh_token: str, expiry: datetime | None):
    creds = Credentials(
        access_token,
        refresh_token=refresh_token,
        client_id=os.environ[_ENV_CLIENT_ID],
        client_secret=os.environ[_ENV_CLIENT_SECRET],
        token_uri=_TOKEN_URI,
        scopes=SCOPES,
        expiry=expiry,
    )
    service = build(_SEARCHCONSOLE_SERVICE, _SEARCHCONSOLE_VERSION, credentials=creds, cache_discovery=False)
    return service, creds


def _creds_snapshot(creds: Credentials) -> dict:
    """Every function below that calls Google must return this alongside
    its own data -- Google's Credentials object mutates token/expiry IN
    PLACE on any auto-refresh triggered mid-call (not only when
    refresh_access_token() is called explicitly), so the caller (routes,
    job handlers) must re-encrypt and save these back to google_connections
    after every call, not only after an explicit refresh -- otherwise the
    DB can end up holding a stale access token while Google has already
    rotated it."""
    return {"access_token": creds.token, "expiry": creds.expiry}


def list_sites(access_token: str, refresh_token: str, expiry: datetime | None = None) -> GSCResult:
    """Wraps service.sites().list().execute(). Gotcha (confirmed against
    Google's live discovery doc): the response field is `siteEntry` (an
    array), not `sites`. An account with zero accessible properties is a
    real, valid state (no_data), not an error."""
    try:
        service, creds = _build_service(access_token, refresh_token, expiry)
        response = service.sites().list().execute()
    except HttpError as e:
        failure_log.failure("google_search_console.list_sites", f"HTTP {e.status_code}")
        return GSCResult(status="error", error=f"Google Sites API error (HTTP {e.status_code}): {e.reason}")
    except RefreshError as e:
        revoked = "invalid_grant" in str(e)
        failure_log.failure("google_search_console.list_sites", str(e), revoked=revoked)
        return GSCResult(status="error", error=str(e), data={"revoked": revoked})
    except Exception as e:
        failure_log.crash("google_search_console.list_sites", e)
        return GSCResult(status="error", error=f"Could not list Search Console properties: {e}")

    entries = response.get("siteEntry") or []
    creds_snapshot = _creds_snapshot(creds)
    if not entries:
        return GSCResult(status="no_data", data={"sites": [], **creds_snapshot})

    sites = [
        {"site_url": entry.get("siteUrl"), "permission_level": entry.get("permissionLevel")}
        for entry in entries
    ]
    return GSCResult(status="ok", data={"sites": sites, **creds_snapshot})
