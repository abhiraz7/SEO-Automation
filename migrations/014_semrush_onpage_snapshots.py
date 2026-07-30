"""
Migration 014: add semrush_onpage_snapshots table -- cached per-SEMrush-
project on-page issue counts, so GET /onpage-semrush reads from the
database instead of burning API units on every page load. Purely
additive. No FK to projects(id): semrush_project_id is SEMrush's own
Projects-API id, independent of our app's Project rows.

Run:  python migrations/014_semrush_onpage_snapshots.py
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

        if "semrush_onpage_snapshots" not in tables:
            con.execute("""
                CREATE TABLE semrush_onpage_snapshots (
                    id INTEGER PRIMARY KEY,
                    semrush_project_id INTEGER NOT NULL UNIQUE,
                    name TEXT,
                    url TEXT,
                    total INTEGER DEFAULT 0,
                    critical INTEGER DEFAULT 0,
                    warnings INTEGER DEFAULT 0,
                    by_category JSON,
                    error TEXT,
                    fetched_at DATETIME
                )
            """)
            print("Created semrush_onpage_snapshots table.")
        else:
            print("semrush_onpage_snapshots already exists -- skipping.")

        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
