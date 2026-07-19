"""
Migration 007: persist market location per tracked keyword.

Adds tracked_keywords.location (ISO code). Until now the location used at
track time wasn't recorded anywhere, so a future refresh job would have had
to guess the market. Backfill: each row inherits its workspace's
default_location (the value a refresh would have used anyway); rows whose
workspace has none get the new app default 'US'. NOTE: for keywords that
were tracked with an explicitly non-default location before this migration
(the UI allowed it), the original choice was never stored and is
unrecoverable -- workspace default is the best available approximation.

Run:  python migrations/007_tracked_keyword_location.py
"""
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO_ROOT, "seo_automation.db")


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(tracked_keywords)")}
        if "location" in cols:
            print("location column already exists -- nothing to do.")
            return
        con.execute("ALTER TABLE tracked_keywords ADD COLUMN location TEXT NOT NULL DEFAULT 'US'")
        updated = con.execute("""
            UPDATE tracked_keywords
            SET location = COALESCE(
                (SELECT w.default_location FROM keyword_workspaces w
                 WHERE w.id = tracked_keywords.workspace_id
                   AND w.default_location IS NOT NULL AND w.default_location != ''),
                'US'
            )
        """).rowcount
        con.commit()
        print(f"Added tracked_keywords.location; backfilled {updated} row(s) from workspace defaults.")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
