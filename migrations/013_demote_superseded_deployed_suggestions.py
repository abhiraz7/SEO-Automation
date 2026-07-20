"""
Migration 013: demote superseded 'deployed' suggestions.

Root cause fixed in code (routes/wordpress.py.deploy_suggestion): deploying
a suggestion never checked whether another suggestion under the same issue
was already status='deployed', so deploying a second/third candidate for
one issue left ALL of them marked 'deployed' -- only the most recent write
is actually live on WordPress, but the UI showed multiple "deployed / Roll
back" cards for a single issue.

This is a one-time correction for rows created before that fix: for every
issue with more than one 'deployed' suggestion, keep the one with the most
recent deployed_at (the one whose value is actually live) and demote the
rest to 'accepted' -- same status a rollback leaves behind, since the
human decision to use that suggestion still stands, only its live deploy
isn't the current one anymore.

Run:  python migrations/013_demote_superseded_deployed_suggestions.py
"""
import os
import sqlite3
import sys
from collections import defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO_ROOT, "seo_automation.db")


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        by_issue = defaultdict(list)
        for row in con.execute("SELECT id, issue_id, deployed_at FROM suggestions WHERE status = 'deployed'"):
            by_issue[row["issue_id"]].append(dict(row))

        demoted = 0
        affected_issues = 0
        for issue_id, rows in by_issue.items():
            if len(rows) < 2:
                continue
            affected_issues += 1
            keep = max(rows, key=lambda r: r["deployed_at"] or "")
            for r in rows:
                if r["id"] == keep["id"]:
                    continue
                con.execute("UPDATE suggestions SET status = 'accepted' WHERE id = ?", (r["id"],))
                demoted += 1
        con.commit()
        print(f"Demoted {demoted} superseded 'deployed' suggestion(s) across {affected_issues} issue(s) to 'accepted'.")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
