"""
Migration 022: Competitor Analysis module foundation -- competitors +
competitor_snapshots tables. ONE competitor record per (project, domain),
reusable by every future Competitor Analysis branch (Keyword Gap, Backlink
Gap, SERP Comparison, etc.) rather than each growing its own list.

Run:  python migrations/022_competitors.py
"""
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO_ROOT, "seo_automation.db")


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS competitors (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id),
                domain TEXT NOT NULL,
                display_name TEXT,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME,
                updated_at DATETIME,
                UNIQUE(project_id, domain)
            )
        """)
        print("Ensured competitors table exists.")

        con.execute("""
            CREATE TABLE IF NOT EXISTS competitor_snapshots (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id),
                competitor_id INTEGER REFERENCES competitors(id),
                organic_keywords INTEGER,
                referring_domains INTEGER,
                total_backlinks INTEGER,
                keywords_source TEXT,
                backlinks_source TEXT,
                error TEXT,
                fetched_at DATETIME
            )
        """)
        print("Ensured competitor_snapshots table exists.")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
