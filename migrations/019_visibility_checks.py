"""
Migration 019: visibility_checks table -- AI Visibility feature. Each row
is one live DataForSEO SERP lookup (fetch_serp) recording whether the
project's domain appears in Google's real AI Overview citations and/or
organic rankings for a query. No invented scoring -- just what the API
returned.

Run:  python migrations/019_visibility_checks.py
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
            CREATE TABLE IF NOT EXISTS visibility_checks (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id),
                query TEXT NOT NULL,
                ai_overview_present BOOLEAN NOT NULL DEFAULT 0,
                brand_in_ai_overview BOOLEAN NOT NULL DEFAULT 0,
                ai_overview_references JSON,
                organic_rank INTEGER,
                competitor_domains JSON,
                raw_error TEXT,
                created_at DATETIME
            )
        """)
        print("Ensured visibility_checks table exists.")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
