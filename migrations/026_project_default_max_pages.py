"""
Migration 026: adds projects.default_max_crawl_pages.

Lets a user save their preferred "Max pages" value (from the on-page
Refresh modal, see routes/onpage_semrush.py) per project instead of
retyping it every crawl. NULL means "never saved" -- callers fall back
to 100.

Run:  python migrations/026_project_default_max_pages.py
"""
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO_ROOT, "seo_automation.db")


def _add_column_if_missing(con, table, column, ddl):
    cols = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
        print(f"Added {table}.{column}")
    else:
        print(f"{table}.{column} already exists, skipping")


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        _add_column_if_missing(con, "projects", "default_max_crawl_pages", "default_max_crawl_pages INTEGER")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
