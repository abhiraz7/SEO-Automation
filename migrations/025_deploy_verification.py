"""
Migration 025: adds post-deploy verification columns to suggestion_revisions.

A deploy was previously considered "done" the moment WordPress accepted the
write. That says nothing about whether the public page actually reflects it
yet (page cache / CDN cache in front of WordPress can serve the old value
for a while). This adds columns for a separate, automatic check -- an
external DataForSEO fetch of the live URL -- run shortly after every
deploy (see app/jobs/handlers/verify_deploy.py).

Run:  python migrations/025_deploy_verification.py
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
        _add_column_if_missing(con, "suggestion_revisions", "verify_status", "verify_status TEXT DEFAULT 'pending'")
        _add_column_if_missing(con, "suggestion_revisions", "verify_checked_at", "verify_checked_at DATETIME")
        _add_column_if_missing(con, "suggestion_revisions", "verify_detail", "verify_detail TEXT")
        con.execute("UPDATE suggestion_revisions SET verify_status = 'pending' WHERE verify_status IS NULL")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
