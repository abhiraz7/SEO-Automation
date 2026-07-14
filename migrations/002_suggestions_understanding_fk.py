"""
Add understanding_id (FK -> page_understanding.id) to the existing suggestions
table, without touching any existing row's data. New column is nullable, so
every suggestion generated before this migration simply gets NULL — there is
nothing to backfill since page_understanding didn't exist when those rows were
created.

Run with:
    python migrations/002_suggestions_understanding_fk.py [path/to/seo_automation.db]

Safe to re-run: skips if the column already exists.
"""
import sqlite3
import sys

DEFAULT_DB_PATH = "seo_automation.db"


def _existing_columns(con, table):
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def migrate(db_path: str) -> None:
    con = sqlite3.connect(db_path)
    try:
        cols = _existing_columns(con, "suggestions")
        if "understanding_id" in cols:
            print("Nothing to migrate: suggestions.understanding_id already exists.")
            return

        con.execute(
            "ALTER TABLE suggestions ADD COLUMN understanding_id INTEGER "
            "REFERENCES page_understanding(id)"
        )
        con.commit()
        print("Added suggestions.understanding_id (nullable FK -> page_understanding.id).")
    finally:
        con.close()


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB_PATH
    migrate(db_path)
