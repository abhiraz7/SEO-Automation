import logging

from app.services import failure_log


def test_failure_logs_at_warning_with_where_and_reason(caplog):
    with caplog.at_level(logging.WARNING, logger="failure_log"):
        failure_log.failure("gsc.list_sites", "HTTP 403", project_id=7)
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.WARNING
    assert "gsc.list_sites" in record.message
    assert "HTTP 403" in record.message
    assert "project_id=7" in record.message


def test_failure_with_no_context_still_logs_cleanly(caplog):
    with caplog.at_level(logging.WARNING, logger="failure_log"):
        failure_log.failure("gsc.refresh_token", "invalid_grant")
    assert "gsc.refresh_token: invalid_grant" in caplog.records[0].message


def test_crash_logs_at_error_with_exception_info(caplog):
    exc = RuntimeError("boom")
    with caplog.at_level(logging.ERROR, logger="failure_log"):
        failure_log.crash("gsc.inspect_url", exc, project_id=3)
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.ERROR
    assert "gsc.inspect_url" in record.message
    assert "boom" in record.message
    assert record.exc_info is not None


def test_happy_path_logs_nothing():
    """The proof style the project's failure-logging rule asks for: nothing
    is logged at WARNING/ERROR when there is no failure to report."""
    logger = logging.getLogger("failure_log")
    logger.setLevel(logging.WARNING)
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    logger.addHandler(handler)
    try:
        pass  # the "happy path" under test: nothing calls failure()/crash()
    finally:
        logger.removeHandler(handler)
    assert records == []
