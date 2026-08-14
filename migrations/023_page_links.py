"""
Migration 023: Link Analyzer foundation -- page_links table. One row per
link DataForSEO's on_page/links endpoint reports for a site-audit task:
broken links, internal/external direction, dofollow/nofollow, anchor text.
Wholesale-replaced per OnPageTask, same discipline as Issue rows.

Run:  python migrations/023_page_links.py
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
            CREATE TABLE IF NOT EXISTS page_links (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id),
                onpage_task_id INTEGER NOT NULL REFERENCES onpage_tasks(id),
                url_from TEXT NOT NULL,
                url_to TEXT NOT NULL,
                link_type TEXT,
                direction TEXT,
                dofollow BOOLEAN,
                is_broken BOOLEAN DEFAULT 0,
                status_code INTEGER,
                anchor_text TEXT,
                is_link_relation_conflict BOOLEAN DEFAULT 0,
                created_at DATETIME
            )
        """)
        print("Ensured page_links table exists.")

        con.execute("CREATE INDEX IF NOT EXISTS idx_page_links_task ON page_links(onpage_task_id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_page_links_project ON page_links(project_id)")
        print("Ensured page_links indexes exist.")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
