"""WARNING+ logs are stored (redacted, with tracebacks) for the Settings page, and a
failure to store one must never break the app. Uses an in-memory database."""
import logging
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import logging_setup, models


@pytest.fixture
def setup():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    handler = logging_setup.DBLogHandler(Session, keep=10)
    log = logging.getLogger("test_app_log")
    log.setLevel(logging.DEBUG)
    log.propagate = False
    log.addHandler(handler)
    yield log, Session, handler
    log.removeHandler(handler)
    engine.dispose()


def rows(Session):
    with Session() as s:
        return s.query(models.AppLog).order_by(models.AppLog.id).all()


def test_warning_and_error_are_stored_with_level_and_logger(setup):
    log, Session, _ = setup
    log.warning("dataforseo.call_failed path=/on_page/x status=40210")
    log.error("boom")
    got = rows(Session)
    assert [(r.level, r.logger) for r in got] == [("WARNING", "test_app_log"), ("ERROR", "test_app_log")]
    assert "dataforseo.call_failed" in got[0].message and got[0].created_at is not None


def test_info_and_debug_are_not_stored(setup):
    log, Session, _ = setup
    log.info("chatty"); log.debug("very chatty")
    assert rows(Session) == []


def test_traceback_is_stored_raw(setup):
    log, Session, _ = setup
    try:
        raise KeyError("ANTHROPIC_API_KEY")
    except KeyError:
        log.exception("suggestions.provider_failed project=3")
    msg = rows(Session)[0].message
    assert "provider_failed" in msg and "Traceback (most recent call last)" in msg and "KeyError" in msg


def test_secrets_are_redacted_before_they_reach_the_database(setup, monkeypatch):
    log, Session, _ = setup
    monkeypatch.setenv("SEMRUSH_API_KEY", "a1b2c3d4e5f60718293a4b5c6d7e8f90")
    log.error("HTTPError for https://api.semrush.com/?type=x&key=a1b2c3d4e5f60718293a4b5c6d7e8f90&domain=y.com")
    msg = rows(Session)[0].message
    assert "a1b2c3d4e5f6" not in msg and "domain=y.com" in msg


def test_table_is_pruned_to_the_newest_rows(setup):
    log, Session, handler = setup
    handler._inserts = 0
    for i in range(120):
        log.warning(f"event {i}")
    kept = rows(Session)
    assert len(kept) <= 10 + logging_setup._PRUNE_EVERY      # pruned every 50 inserts down to `keep`
    assert kept[-1].message == "event 119"                    # newest survives


def test_a_broken_database_never_raises_into_the_caller():
    def broken():
        raise RuntimeError("database is locked")
    handler = logging_setup.DBLogHandler(broken)
    log = logging.getLogger("test_app_log_broken")
    log.propagate = False
    log.addHandler(handler)
    try:
        log.error("this must not raise")          # would throw if emit() leaked the error
    finally:
        log.removeHandler(handler)


def test_full_pipeline_logger_to_queue_to_background_thread_to_database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    before = {n: list(logging.getLogger(n).handlers) for n in ("", "uvicorn")}
    try:
        logging_setup.start_db_logging(Session)
        assert logging_setup.start_db_logging(Session) is logging_setup._listener      # idempotent
        logging.getLogger("uvicorn.error").error("Exception in ASGI application")       # uvicorn does not propagate to root
        logging.getLogger("suggestions").warning("suggestions.provider_failed project=3")
        # stop() puts a sentinel on the queue and joins the worker thread, so every record
        # already queued has been written when it returns -- no sleeping, no flakiness.
        logging_setup.stop_db_logging()
        stored = {r.logger: r.message for r in rows(Session)}
        assert "Exception in ASGI application" in stored["uvicorn.error"]
        assert "provider_failed" in stored["suggestions"]
    finally:
        logging_setup.stop_db_logging()
        for name, handlers in before.items():
            lg = logging.getLogger(name)
            for h in list(lg.handlers):
                if h not in handlers:
                    lg.removeHandler(h)
        engine.dispose()


def test_console_formatter_redacts_message_and_traceback(monkeypatch):
    monkeypatch.setenv("SOME_SERVICE_TOKEN", "tok_live_0123456789abcdef")
    fmt = logging_setup.RedactingFormatter(logging_setup.LOG_FORMAT)
    try:
        raise RuntimeError("upstream said token=tok_live_0123456789abcdef")
    except RuntimeError:
        import sys
        rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "call failed for https://h/?key=abcdef1234567890", (), sys.exc_info())
    line = fmt.format(rec)
    assert "0123456789abcdef" not in line and "abcdef1234567890" not in line
    assert "call failed" in line and "Traceback (most recent call last)" in line


def test_test_suite_keeps_log_storage_off_the_real_database():
    assert os.environ["APP_LOG_TO_DB"] == "0"
    queue_handlers = [h for h in logging.getLogger().handlers if isinstance(h, logging.handlers.QueueHandler)]
    assert queue_handlers == []
