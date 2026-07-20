"""
Migration 012: content_hash column + de-duplicate + unique constraint on
suggestions.

Root cause fixed here (see AgentLog audit): _generate_and_store's
delete-before-insert only clears undecided suggestions, so regenerating on
an issue that already has an accepted/edited suggestion can insert a new
row with textually identical content -- two rows, same wording, both
shown as separate cards. There was no constraint stopping this.

Steps, in order (each depends on the previous):
  1. Add suggestions.content_hash (nullable at first).
  2. Backfill content_hash for every existing row: sha256 of the row's own
     `content` field, normalized (trim + collapse whitespace + casefold) --
     same normalization app/routes/suggestions.py.content_hash() uses, so
     rows inserted before and after this migration hash identically.
  3. For every (issue_id, content_hash) group with more than one row:
     keep the oldest DECIDED row (accepted/edited/deployed) if any exist
     in the group, else the oldest row overall; delete the rest -- UNLESS
     a row-to-delete is referenced by suggestion_revisions (a real
     deploy/rollback happened against it), in which case it's left alone
     and reported instead of silently orphaning revision history.
  4. Create a UNIQUE index on (issue_id, content_hash) so this class of
     duplicate can't reappear. If any duplicate group survives step 3
     (protected by revision history), index creation is attempted and,
     if it fails, the failure is reported rather than crashing the
     migration -- that residual case needs a human decision, not a
     silent skip or a hard stop.

Run:  python migrations/012_dedupe_and_hash_suggestions.py
"""
import hashlib
import os
import sqlite3
import sys
from collections import defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO_ROOT, "seo_automation.db")

DECIDED_STATUSES = ("accepted", "edited", "deployed")


def _normalize(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def _content_hash(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(suggestions)")}
        if "content_hash" not in cols:
            con.execute("ALTER TABLE suggestions ADD COLUMN content_hash TEXT")
            con.commit()
            print("Added suggestions.content_hash.")
        else:
            print("suggestions.content_hash already exists.")

        rows = con.execute("SELECT id, content FROM suggestions").fetchall()
        for row in rows:
            con.execute(
                "UPDATE suggestions SET content_hash = ? WHERE id = ?",
                (_content_hash(row["content"]), row["id"]),
            )
        con.commit()
        print(f"Backfilled content_hash for {len(rows)} row(s).")

        groups = defaultdict(list)
        for row in con.execute("SELECT id, issue_id, content_hash, status, created_at FROM suggestions"):
            groups[(row["issue_id"], row["content_hash"])].append(dict(row))

        removed = 0
        affected_issues = 0
        protected = []
        for (issue_id, c_hash), members in groups.items():
            if len(members) < 2:
                continue
            affected_issues += 1
            decided = [m for m in members if m["status"] in DECIDED_STATUSES]
            pool = decided or members
            keep = min(pool, key=lambda m: m["created_at"] or "")
            for m in members:
                if m["id"] == keep["id"]:
                    continue
                has_revision = con.execute(
                    "SELECT 1 FROM suggestion_revisions WHERE suggestion_id = ? LIMIT 1", (m["id"],)
                ).fetchone()
                if has_revision:
                    protected.append({"id": m["id"], "issue_id": issue_id})
                    continue
                con.execute("DELETE FROM suggestions WHERE id = ?", (m["id"],))
                removed += 1
        con.commit()
        print(f"Removed {removed} duplicate suggestion(s) across {affected_issues} issue(s).")
        if protected:
            print(f"Skipped {len(protected)} duplicate(s) protected by revision history (manual review needed): {protected}")

        try:
            con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_suggestions_issue_content_hash "
                "ON suggestions(issue_id, content_hash)"
            )
            con.commit()
            print("Created unique index on (issue_id, content_hash).")
        except sqlite3.IntegrityError as e:
            print(f"WARNING: could not create unique index -- duplicate rows remain (likely revision-protected): {e}")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
