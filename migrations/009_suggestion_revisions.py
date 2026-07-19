"""
Migration 009: add suggestion_revisions table (Task 3.3 / V11).

Purely additive. One row per successful deploy (or rollback) of a
suggestion to WordPress -- see models.SuggestionRevision docstring.

Run:  python migrations/009_suggestion_revisions.py
"""
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO_ROOT, "seo_automation.db")


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "suggestion_revisions" in tables:
            print("suggestion_revisions already exists -- skipping.")
            return
        con.execute("""
            CREATE TABLE suggestion_revisions (
                id INTEGER PRIMARY KEY,
                suggestion_id INTEGER NOT NULL REFERENCES suggestions(id),
                project_id INTEGER NOT NULL REFERENCES projects(id),
                field_name TEXT NOT NULL,
                before_value TEXT,
                after_value TEXT,
                wp_post_id INTEGER NOT NULL,
                deployed_via TEXT NOT NULL,
                deployed_at DATETIME,
                rolled_back_at DATETIME,
                deploy_result_raw JSON
            )
        """)
        con.execute("CREATE INDEX ix_suggestion_revisions_project_id ON suggestion_revisions(project_id)")
        con.commit()
        print("Created suggestion_revisions table.")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
