"""
Migration 020: adds DataForSEO-only columns to backlink_snapshots
(spam_score, broken_backlinks, tld_distribution, platform_distribution) --
verified live against the real backlinks/summary/live response (2026-08-09).
NULL on rows fetched from Semrush, which has no equivalent fields.

Run:  python migrations/020_backlink_snapshot_dataforseo_extras.py
"""
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO_ROOT, "seo_automation.db")

NEW_COLUMNS = [
    ("spam_score", "INTEGER"),
    ("broken_backlinks", "INTEGER"),
    ("tld_distribution", "JSON"),
    ("platform_distribution", "JSON"),
]


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        existing = {row[1] for row in con.execute("PRAGMA table_info(backlink_snapshots)")}
        for name, coltype in NEW_COLUMNS:
            if name in existing:
                print(f"Column {name} already exists, skipping.")
                continue
            con.execute(f"ALTER TABLE backlink_snapshots ADD COLUMN {name} {coltype}")
            print(f"Added column {name} ({coltype}) to backlink_snapshots.")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
