"""
Migration 004: add keyword_snapshots.trend_points.

Stores the provider's 12-month relative-volume series ("1.00,0.82,...") so the
Overview table can render a sparkline without a live API call on every page
load. Purely additive -- a plain ADD COLUMN, nullable, no backfill (old
snapshots simply have no sparkline until their keyword is refreshed).

Run:  python migrations/004_snapshot_trend_points.py
"""
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO_ROOT, "seo_automation.db")


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        cols = [c[1] for c in con.execute("PRAGMA table_info(keyword_snapshots)")]
        if "trend_points" in cols:
            print("trend_points already exists -- nothing to do.")
            return
        con.execute("ALTER TABLE keyword_snapshots ADD COLUMN trend_points TEXT")
        con.commit()
        print("Added keyword_snapshots.trend_points.")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
