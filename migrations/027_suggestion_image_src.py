"""
Migration 027: adds suggestions.image_src.

An image_alt issue is one row per PAGE but can list several missing-alt
images (see routes/onpage_semrush.py._missing_alt_images). Suggestion was
previously scoped only to issue_id, so a page with 3 missing-alt images had
one shared, ambiguous suggestion pool -- no way to tell which image a given
suggestion's alt text was actually for. image_src scopes a suggestion to one
specific image within a multi-image issue; NULL for every non-image_alt
category (and for image_alt issues with exactly one image, harmlessly None
until the frontend starts sending it).

Run:  python migrations/027_suggestion_image_src.py
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
        _add_column_if_missing(con, "suggestions", "image_src", "image_src TEXT")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
