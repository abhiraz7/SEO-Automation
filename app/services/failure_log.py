"""
Shared failure-logging helper -- the concrete implementation of the
project-wide "every failure must be logged, not just shown" rule.

This did not exist as code anywhere in the repo before Feature 3 (Google
Search Console); it was only a standing convention. Building it here because
GSC is the first feature whose task list requires it explicitly, but it is
deliberately general-purpose -- any provider module (semrush.py,
dataforseo.py, wordpress.py, google_search_console.py, job handlers) can
import and use it, the same way schema_check.py already uses
logging.getLogger(name) + caplog for its own tests.

Two functions, two different situations:

- failure(where, reason, **context) -- an EXPECTED failure: a call returned
  a non-2xx status, a token was revoked, a required env var was missing.
  Logged at WARNING. This is not a bug, it's the ok/no_data/error discipline
  working as designed -- something the caller already turned into a
  structured error result. Logging it here just means it is not *only*
  visible to whoever happens to be looking at that one response.

- crash(where, exc, **context) -- an UNEXPECTED failure: an exception that
  escaped normal handling (a bug, a library behaving unexpectedly, a network
  error not already caught). Logged at ERROR with exc_info, so a stack trace
  ends up in the logs.

Both take a required `where` (a short string identifying the call site --
module.function, e.g. "google_search_console.list_sites") and free-form
keyword context. NEVER pass a secret (token, password, API key) or draft/
user-authored content as context -- these are LOG lines, not encrypted
storage, and may end up in log aggregation tools with different retention/
access rules than the DB. Pass identifiers (project_id, site_url, status
code) instead.
"""
import logging
from typing import Any

logger = logging.getLogger("failure_log")


def _format(where: str, reason: str, context: dict[str, Any]) -> str:
    ctx = " ".join(f"{k}={v!r}" for k, v in context.items())
    return f"{where}: {reason}" + (f" ({ctx})" if ctx else "")


def failure(where: str, reason: str, **context: Any) -> None:
    """Log an expected/handled failure. Call once, at the point the
    ok/no_data/error outcome is decided -- not at every layer that re-raises
    or re-wraps it."""
    logger.warning(_format(where, reason, context))


def crash(where: str, exc: BaseException, **context: Any) -> None:
    """Log an unexpected exception, with traceback, at the boundary that
    caught it."""
    logger.error(_format(where, str(exc), context), exc_info=exc)
