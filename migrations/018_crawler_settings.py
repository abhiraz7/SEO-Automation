"""
Migration 018: crawler_settings table -- a single row (id=1) that's a
master on/off switch for the custom crawler+audit.py pipeline. Seeded
enabled=1 so nothing changes for existing projects until someone
deliberately visits the hidden /settings/crawler page and flips it off.

Run:  python migrations/018_crawler_settings.py
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
            CREATE TABLE IF NOT EXISTS crawler_settings (
                id INTEGER PRIMARY KEY,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                updated_at DATETIME
            )
        """)
        row = con.execute("SELECT id FROM crawler_settings WHERE id = 1").fetchone()
        if not row:
            con.execute("INSERT INTO crawler_settings (id, enabled) VALUES (1, 1)")
            print("Seeded crawler_settings row (enabled=1).")
        else:
            print("crawler_settings row already exists -- skipping.")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
