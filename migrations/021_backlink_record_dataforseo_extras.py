"""
Migration 021: adds domain_rank/spam_score to backlink_records --
DataForSEO's per-link backlinks/backlinks/live response includes
domain_from_rank and backlink_spam_score per row (verified live,
2026-08-09); Semrush's per-link CSV report has no equivalent, so these
stay NULL on Semrush-sourced rows.

Run:  python migrations/021_backlink_record_dataforseo_extras.py
"""
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO_ROOT, "seo_automation.db")

NEW_COLUMNS = [
    ("domain_rank", "INTEGER"),
    ("spam_score", "INTEGER"),
]


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        existing = {row[1] for row in con.execute("PRAGMA table_info(backlink_records)")}
        for name, coltype in NEW_COLUMNS:
            if name in existing:
                print(f"Column {name} already exists, skipping.")
                continue
            con.execute(f"ALTER TABLE backlink_records ADD COLUMN {name} {coltype}")
            print(f"Added column {name} ({coltype}) to backlink_records.")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
