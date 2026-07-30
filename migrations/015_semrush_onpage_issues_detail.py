"""
Migration 015: add issues_detail column to semrush_onpage_snapshots --
per-affected-page rows (category/severity/message/url) so the
/onpage-semrush view can render a project_detail.html-style "Issues by
category" accordion instead of just aggregate counts. Purely additive.

Run:  python migrations/015_semrush_onpage_issues_detail.py
"""
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO_ROOT, "seo_automation.db")


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(semrush_onpage_snapshots)")}
        if "issues_detail" not in cols:
            con.execute("ALTER TABLE semrush_onpage_snapshots ADD COLUMN issues_detail JSON")
            print("Added issues_detail column.")
        else:
            print("issues_detail already exists -- skipping.")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
