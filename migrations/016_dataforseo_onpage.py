"""
Migration 016: DataForSEO on-page ingestion -- adds a "source" column to
pages (default "crawler", so all existing rows keep their current meaning)
plus onpage_task_id/word_count/onpage_score/checks, and a new onpage_tasks
table for tracking DataForSEO site-wide crawl tasks. Lets /onpage-semrush
show only source != "crawler" pages/issues without touching crawler data.

Run:  python migrations/016_dataforseo_onpage.py
"""
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO_ROOT, "seo_automation.db")


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(pages)")}
        if "source" not in cols:
            con.execute("ALTER TABLE pages ADD COLUMN source TEXT NOT NULL DEFAULT 'crawler'")
            print("Added pages.source (backfilled 'crawler' for existing rows).")
        else:
            print("pages.source already exists -- skipping.")
        if "onpage_task_id" not in cols:
            con.execute("ALTER TABLE pages ADD COLUMN onpage_task_id INTEGER")
            print("Added pages.onpage_task_id.")
        if "word_count" not in cols:
            con.execute("ALTER TABLE pages ADD COLUMN word_count INTEGER")
            print("Added pages.word_count.")
        if "onpage_score" not in cols:
            con.execute("ALTER TABLE pages ADD COLUMN onpage_score INTEGER")
            print("Added pages.onpage_score.")
        if "checks" not in cols:
            con.execute("ALTER TABLE pages ADD COLUMN checks JSON")
            print("Added pages.checks.")

        con.execute("""
            CREATE TABLE IF NOT EXISTS onpage_tasks (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id),
                dataforseo_task_id TEXT NOT NULL,
                max_crawl_pages INTEGER DEFAULT 20,
                status TEXT NOT NULL DEFAULT 'posted',
                pages_crawled INTEGER,
                error TEXT,
                finished_at DATETIME,
                created_at DATETIME
            )
        """)
        print("Ensured onpage_tasks table exists.")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
