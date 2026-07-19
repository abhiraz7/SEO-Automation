"""
Migration 010: add backlink_snapshots and backlink_records tables (Task 5.1
+ 5.2 groundwork). Purely additive.

Run:  python migrations/010_backlinks.py
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

        if "backlink_snapshots" not in tables:
            con.execute("""
                CREATE TABLE backlink_snapshots (
                    id INTEGER PRIMARY KEY,
                    project_id INTEGER NOT NULL REFERENCES projects(id),
                    authority_score INTEGER,
                    referring_domains INTEGER,
                    total_backlinks INTEGER,
                    follow_links INTEGER,
                    nofollow_links INTEGER,
                    source TEXT NOT NULL,
                    fetched_at DATETIME
                )
            """)
            con.execute("CREATE INDEX ix_backlink_snapshots_project_id ON backlink_snapshots(project_id)")
            print("Created backlink_snapshots table.")
        else:
            print("backlink_snapshots already exists -- skipping.")

        if "backlink_records" not in tables:
            con.execute("""
                CREATE TABLE backlink_records (
                    id INTEGER PRIMARY KEY,
                    project_id INTEGER NOT NULL REFERENCES projects(id),
                    source_url TEXT NOT NULL,
                    target_url TEXT NOT NULL,
                    anchor_text TEXT,
                    is_follow BOOLEAN,
                    first_seen_at DATETIME,
                    last_seen_at DATETIME,
                    lost_at DATETIME,
                    UNIQUE (project_id, source_url, target_url)
                )
            """)
            con.execute("CREATE INDEX ix_backlink_records_project_id ON backlink_records(project_id)")
            print("Created backlink_records table.")
        else:
            print("backlink_records already exists -- skipping.")

        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
