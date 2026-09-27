"""
One way to record a failure, so every failure can be found with one search.

    failure(logger, "optimizer.run_failed", run=12, project=3, reason="...")   # WARNING
    crash(logger, "optimizer.run_crashed", run=12)                              # ERROR + traceback (inside an except)
    note(logger, "optimizer.no_change", run=12)                                 # INFO: not a failure

    ->  optimizer.run_failed run=12 project=3 reason="The search evidence could not be fetched: 402"

Two levels, on purpose:
  failure  WARNING  the system worked, but something it depends on or produced was
                    unusable: a search-provider error, an AI answer that could not be
                    used, a suggestion the checks blocked, an attempt to approve one.
  crash    ERROR    OUR code broke (an unexpected exception, a validation check that
                    raised). Logged with the traceback, because that is a bug to fix.

The message is `event key=value key=value`: the event name is greppable, values that
contain spaces are quoted, and everything is one line, so a failure survives whatever
collects the logs. Values are truncated, and any field whose NAME looks like a secret
(key, token, password...) is replaced by [redacted]: a helper that logs must never be
the way a credential leaks.

What is deliberately NOT logged: page text, drafts, prompts or model answers (only the
reason a step failed).
"""
import json
import logging
import re

MAX_VALUE_CHARS = 300
# Matches whole name-parts ('api_key', 'access_token', 'client_secret'), NOT substrings: a
# field called 'keyword' (this is an SEO tool) or 'tokens_used' is not a secret.
_SECRET_NAME = re.compile(r"(^|[_\-])(api_?key|key|token|secret|password|passwd|authorization|credentials?|cookie)($|[_\-])", re.I)
_PLAIN = re.compile(r"[\w./:@#%+\-]+", re.UNICODE)


def _one_line(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= MAX_VALUE_CHARS else text[:MAX_VALUE_CHARS - 1] + "…"


def _render(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        value = ", ".join(_one_line(v) for v in value)
    text = _one_line(value)
    return text if text and _PLAIN.fullmatch(text) else json.dumps(text, ensure_ascii=False)


def format_event(event: str, **fields) -> str:
    parts = [event]
    for name, value in fields.items():
        if value is None:
            continue
        parts.append(f"{name}={'[redacted]' if _SECRET_NAME.search(name) else _render(value)}")
    return " ".join(parts)


def failure(logger: logging.Logger, event: str, **fields) -> None:
    logger.warning(format_event(event, **fields))


def crash(logger: logging.Logger, event: str, **fields) -> None:
    """Call from inside an `except` block: records the traceback."""
    logger.exception(format_event(event, **fields))


def note(logger: logging.Logger, event: str, **fields) -> None:
    logger.info(format_event(event, **fields))
