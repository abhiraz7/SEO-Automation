"""
Logging setup for the app.

1. Console: every WARNING and above becomes one line with a timestamp, the level and
   the logger name (Python's fallback handler used to print bare message text, and INFO
   from libraries -- every outgoing HTTP request -- would bury real failures, so the
   level is WARNING on purpose). Only added when the root logger has none, so a host
   that configures logging itself (tests, uvicorn config) is left alone.

2. Database: the same records are also stored in `app_logs` so the Settings page can
   show API/provider errors raw. Writes go through a queue and a background thread, so
   a slow or locked SQLite file can never delay or break a request, and a failure to
   store a log is swallowed (a logger must not be the thing that breaks the app).
   Every message goes through redact() first, so the table never holds a credential.
   Turn it off with APP_LOG_TO_DB=0 (the test suite does).
"""
import atexit
import logging
import logging.handlers
import os
import queue
from datetime import datetime, timezone

from .redact import redact

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
MAX_STORED_MESSAGE = 20000     # characters; a traceback fits, a runaway dump does not
KEEP_NEWEST = 2000             # rows kept in app_logs
_PRUNE_EVERY = 50              # inserts between size checks

_listener: "logging.handlers.QueueListener | None" = None


class DBLogHandler(logging.Handler):
    """Stores each record (message + traceback, redacted) as an app_logs row."""

    def __init__(self, session_factory, keep: int = KEEP_NEWEST, level: int = logging.WARNING):
        super().__init__(level)
        self._session_factory = session_factory
        self._keep = keep
        self._inserts = 0
        self.setFormatter(logging.Formatter("%(message)s"))   # format() appends the traceback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from . import models   # imported lazily: models -> database must be importable first

            text = redact(self.format(record))[:MAX_STORED_MESSAGE]
            with self._session_factory() as session:
                session.add(models.AppLog(
                    level=record.levelname,
                    logger=record.name,
                    message=text,
                    created_at=datetime.fromtimestamp(record.created, timezone.utc),
                ))
                session.commit()
                self._inserts += 1
                if self._inserts % _PRUNE_EVERY == 0:
                    self._prune(session, models)
        except Exception:
            pass   # never log from a log handler, never break the app

    def _prune(self, session, models) -> None:
        newest = session.query(models.AppLog.id).order_by(models.AppLog.id.desc()).offset(self._keep).first()
        if newest:
            session.query(models.AppLog).filter(models.AppLog.id <= newest[0]).delete(synchronize_session=False)
            session.commit()


def start_db_logging(session_factory=None) -> "logging.handlers.QueueListener | None":
    """Attach the queue -> database pipeline to the root logger and to `uvicorn`
    (uvicorn's own loggers do not propagate to root, and that is where unhandled
    exceptions are logged). Idempotent."""
    global _listener
    if _listener is not None:
        return _listener
    if session_factory is None:
        from .database import SessionLocal
        session_factory = SessionLocal

    q: queue.Queue = queue.Queue(-1)
    _listener = logging.handlers.QueueListener(q, DBLogHandler(session_factory), respect_handler_level=True)
    _listener.start()
    atexit.register(stop_db_logging)

    queue_handler = logging.handlers.QueueHandler(q)
    queue_handler.setLevel(logging.WARNING)
    for name in ("", "uvicorn"):
        logging.getLogger(name).addHandler(queue_handler)
    return _listener


def stop_db_logging() -> None:
    global _listener
    if _listener is not None:
        _listener.stop()
        _listener = None


class RedactingFormatter(logging.Formatter):
    """Console formatter that redacts the finished line (message and traceback)."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging() -> None:
    logging.basicConfig(level=logging.WARNING, format=LOG_FORMAT)
    for handler in logging.getLogger().handlers:      # the console handler basicConfig just added
        if type(handler) is logging.StreamHandler:
            handler.setFormatter(RedactingFormatter(LOG_FORMAT))
    if os.environ.get("APP_LOG_TO_DB", "1") != "0":
        start_db_logging()
