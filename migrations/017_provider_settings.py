"""
Migration 017: provider_settings table -- lets the new /settings page toggle
which on-page data source (DataForSEO or SEMrush) is active for
/onpage-semrush. Seeds DataForSEO as the active provider (the only one
currently verified working end-to-end; SEMrush's account is at zero API
units per AgentLog, so it starts off).

Run:  python migrations/017_provider_settings.py
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
            CREATE TABLE IF NOT EXISTS provider_settings (
                provider TEXT PRIMARY KEY,
                enabled BOOLEAN NOT NULL DEFAULT 0,
                updated_at DATETIME
            )
        """)
        existing = {row[0] for row in con.execute("SELECT provider FROM provider_settings")}
        if "dataforseo" not in existing:
            con.execute("INSERT INTO provider_settings (provider, enabled) VALUES ('dataforseo', 1)")
            print("Seeded dataforseo as active provider.")
        if "semrush" not in existing:
            con.execute("INSERT INTO provider_settings (provider, enabled) VALUES ('semrush', 0)")
            print("Seeded semrush as inactive provider.")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
