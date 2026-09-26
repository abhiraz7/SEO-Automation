"""
Does the live database have every column the models expect?

Why this exists: the deploy runs migrations in a separate container while the
old app container still has the SQLite database open in WAL mode. The
migration reported "Added suggestion_revisions.verify_status" on two deploys in
a row, yet the columns were missing afterwards -- the change was left in a
container-local write-ahead file that vanished when the migration container was
removed. Nothing noticed until a page that read those columns returned 500.

This makes that kind of drift visible: it is logged loudly at startup and
reported by GET /version, which the deploy pipeline can poll and fail on.

It only DETECTS. It never alters the database: repairing schema is a migration,
run deliberately.
"""
import logging

from sqlalchemy import inspect

logger = logging.getLogger("schema_check")


def find_schema_drift(engine) -> dict[str, list[str]]:
    """{table: [missing column names]} for every model table; a table that does
    not exist at all is reported as ["<table missing>"]. Empty dict = no drift."""
    from .database import Base  # imported here so importing this module has no side effects

    inspector = inspect(engine)
    drift: dict[str, list[str]] = {}
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            drift[table.name] = ["<table missing>"]
            continue
        have = {col["name"] for col in inspector.get_columns(table.name)}
        gone = [col.name for col in table.columns if col.name not in have]
        if gone:
            drift[table.name] = gone
    return drift


def schema_report(engine) -> dict:
    """Never raises: a problem reading the schema is itself reported, as ok=None
    ("could not tell"), never as ok=True."""
    try:
        drift = find_schema_drift(engine)
    except Exception as exc:  # noqa: BLE001 -- a status endpoint must not take the app down
        logger.exception("schema check failed")
        return {"ok": None, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": not drift, "missing": drift}


def log_drift_at_startup(engine) -> None:
    report = schema_report(engine)
    if report["ok"] is False:
        logger.error(
            "DATABASE SCHEMA DRIFT: the database is missing columns the code expects: %s. "
            "Pages that read them will fail. Run the matching migration(s) in migrations/.",
            report["missing"],
        )
