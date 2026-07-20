"""
Migration 011: cache each page's resolved WordPress post ID.

Adds pages.wp_post_id (nullable) and pages.wp_post_type (nullable). Deploying
a suggestion to WordPress needs a numeric post_id, which previously had to be
typed in by hand every time (the claude-wp-mcp plugin has no "find post by
URL" tool). Resolution now happens automatically during crawl (see
routes/crawl.py's _maybe_resolve_wp_post_id, using WordPress's own public
core REST API) and is cached here so deploy doesn't need to ask. No backfill
-- existing pages get NULL and are resolved on their next crawl/re-audit.

Run:  python migrations/011_page_wp_post_id.py
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
        if "wp_post_id" in cols and "wp_post_type" in cols:
            print("wp_post_id/wp_post_type columns already exist -- nothing to do.")
            return
        if "wp_post_id" not in cols:
            con.execute("ALTER TABLE pages ADD COLUMN wp_post_id INTEGER")
        if "wp_post_type" not in cols:
            con.execute("ALTER TABLE pages ADD COLUMN wp_post_type TEXT")
        con.commit()
        print("Added pages.wp_post_id and pages.wp_post_type.")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
