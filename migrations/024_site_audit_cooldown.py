"""
Migration 024: site_audit_settings table -- singleton row (id=1) holding
the minimum-hours cooldown between DataForSEO Site Audit runs for the same
project. Added after a real incident: a double-clicked "Refresh now"
button fired 13 billed crawls of the same 20 pages within 4 seconds.

Run:  python migrations/024_site_audit_cooldown.py
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
            CREATE TABLE IF NOT EXISTS site_audit_settings (
                id INTEGER PRIMARY KEY,
                cooldown_hours INTEGER NOT NULL DEFAULT 24,
                updated_at DATETIME
            )
        """)
        print("Ensured site_audit_settings table exists.")

        con.execute("INSERT OR IGNORE INTO site_audit_settings (id, cooldown_hours) VALUES (1, 24)")
        print("Ensured default cooldown row (24h) exists.")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
