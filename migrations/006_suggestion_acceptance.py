"""
Migration 006: acceptance tracking on suggestions (V6 / Task 3.1).

Adds status/edited_content/accepted_at/deployed_at to suggestions. Purely
additive ALTER TABLEs; every existing row backfills to status='pending',
which is accurate -- nothing recorded a decision before this existed.

This is the first piece of the learning-dataset vision that the original
V1-V11 roadmap called the product's moat: from here on, user decisions
are recorded instead of discarded.

Run:  python migrations/006_suggestion_acceptance.py
"""
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO_ROOT, "seo_automation.db")

NEW_COLUMNS = [
    ("status", "TEXT NOT NULL DEFAULT 'pending'"),
    ("edited_content", "TEXT"),
    ("accepted_at", "DATETIME"),
    ("deployed_at", "DATETIME"),
]


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        existing = {row[1] for row in con.execute("PRAGMA table_info(suggestions)")}
        added = []
        for name, ddl in NEW_COLUMNS:
            if name not in existing:
                con.execute(f"ALTER TABLE suggestions ADD COLUMN {name} {ddl}")
                added.append(name)
        con.commit()
        print(f"Added columns: {added}" if added else "All columns already present -- nothing to do.")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
