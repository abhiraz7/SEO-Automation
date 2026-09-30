"""
Migration 028: adds issues.impact, issues.score_eligible,
issues.classification_version.

Backs the new app/audit_classification.py registry: every Issue row gets a
materialized snapshot of how it was classified *at the time the audit ran*,
rather than deriving that from a live code lookup. If the registry's rules
change later (e.g. "missing canonical" moves from score_eligible to not),
old rows keep their original classification -- classification_version lets
us tell which policy version produced a given row instead of silently
reinterpreting historical audits.

score_eligible defaults to 1 (true) for existing rows: today's scoring
formula (app/audit.py:page_score, app/routes/projects.py:134,
app/templates/onpage_semrush.html:88) already counts every stored issue
toward the score, so this default preserves current behavior for anything
audited before this migration ran. classification_version defaults to 0
for existing rows specifically so they're distinguishable from anything
classified under registry version 1 going forward.

Run:  python migrations/028_issue_classification.py
"""
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO_ROOT, "seo_automation.db")


def _add_column_if_missing(con, table, column, ddl):
    cols = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
        print(f"Added {table}.{column}")
    else:
        print(f"{table}.{column} already exists, skipping")


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        _add_column_if_missing(con, "issues", "impact", "impact TEXT DEFAULT 'medium'")
        _add_column_if_missing(con, "issues", "score_eligible", "score_eligible INTEGER DEFAULT 1")
        _add_column_if_missing(con, "issues", "classification_version", "classification_version INTEGER DEFAULT 0")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
