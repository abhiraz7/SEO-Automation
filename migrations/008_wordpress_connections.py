"""
Migration 008: add wordpress_connections table (Task 3.2 / V11).

Purely additive. One row per project, api_token stored Fernet-encrypted
(see app/wordpress.py) -- never plaintext, never logged.

Run:  python migrations/008_wordpress_connections.py
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
        if "wordpress_connections" in tables:
            print("wordpress_connections already exists -- skipping.")
            return
        con.execute("""
            CREATE TABLE wordpress_connections (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL UNIQUE REFERENCES projects(id),
                site_url TEXT NOT NULL,
                api_token TEXT NOT NULL,
                is_staging BOOLEAN DEFAULT 1,
                last_verified_at DATETIME,
                last_verify_ok BOOLEAN,
                created_at DATETIME
            )
        """)
        con.commit()
        print("Created wordpress_connections table.")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
