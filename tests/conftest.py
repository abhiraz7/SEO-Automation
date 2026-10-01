"""Shared test setup.

The app stores WARNING+ logs in its own database (logging_setup.start_db_logging).
Tests must never write into the developer's real seo_automation.db, so that
pipeline is switched off before any test imports app.main. The tests that cover
it build their own handler on an in-memory database."""
import os

os.environ["APP_LOG_TO_DB"] = "0"
