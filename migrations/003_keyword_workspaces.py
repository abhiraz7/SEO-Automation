"""
Detach Keyword Research from projects: introduce keyword_workspaces and move
tracked_keywords / saved_keywords from project_id to workspace_id.

Steps:
    1. Create keyword_workspaces.
    2. Backfill one workspace per project that has any keyword data
       (name = project name, project_id = project id, default_location = IN).
    3. Rebuild tracked_keywords and saved_keywords with workspace_id in place
       of project_id, copying every row across and mapping project -> workspace.
    4. Verify row counts match before committing; any mismatch rolls back.

NOTE -- deviation from the spec's "add column now, drop project_id in a later
pass": project_id is NOT NULL in the existing tables and SQLite cannot relax
a NOT NULL constraint in place, so new inserts (which no longer know a
project_id) would fail during the interim. A single verified table rebuild
inside one transaction is the SQLite-idiomatic equivalent; the row-count
check before COMMIT is the same discipline, just compressed into one step.

Run with:
    python migrations/003_keyword_workspaces.py [path/to/seo_automation.db]

Safe to re-run: skips if tracked_keywords already has workspace_id.
"""
import sqlite3
import sys

DEFAULT_DB_PATH = "seo_automation.db"


def _existing_columns(con, table):
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def _table_exists(con, table):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def migrate(db_path: str) -> None:
    con = sqlite3.connect(db_path)
    try:
        if not _table_exists(con, "tracked_keywords"):
            print("Nothing to migrate: tracked_keywords does not exist yet "
                  "(create_all will build the new schema directly).")
            return
        if "workspace_id" in _existing_columns(con, "tracked_keywords"):
            print("Nothing to migrate: tracked_keywords already has workspace_id.")
            return

        con.execute("BEGIN")

        # 1. New container table.
        con.execute("""
            CREATE TABLE IF NOT EXISTS keyword_workspaces (
                id INTEGER PRIMARY KEY,
                name VARCHAR NOT NULL,
                default_location VARCHAR DEFAULT 'IN',
                project_id INTEGER REFERENCES projects(id),
                created_at DATETIME
            )
        """)

        # 2. One workspace per project that actually has keyword data.
        project_ids = {
            row[0] for row in con.execute("SELECT DISTINCT project_id FROM tracked_keywords")
        } | {
            row[0] for row in con.execute("SELECT DISTINCT project_id FROM saved_keywords")
        }
        workspace_by_project = {}
        for pid in sorted(project_ids):
            name_row = con.execute("SELECT name FROM projects WHERE id = ?", (pid,)).fetchone()
            name = name_row[0] if name_row else f"Project {pid}"
            cur = con.execute(
                "INSERT INTO keyword_workspaces (name, default_location, project_id, created_at) "
                "VALUES (?, 'IN', ?, datetime('now'))",
                (name, pid),
            )
            workspace_by_project[pid] = cur.lastrowid

        # 3. Rebuild both keyword tables with workspace_id (see NOTE above).
        old_tracked = con.execute("SELECT COUNT(*) FROM tracked_keywords").fetchone()[0]
        old_saved = con.execute("SELECT COUNT(*) FROM saved_keywords").fetchone()[0]

        con.execute("""
            CREATE TABLE tracked_keywords_new (
                id INTEGER PRIMARY KEY,
                workspace_id INTEGER NOT NULL REFERENCES keyword_workspaces(id),
                keyword VARCHAR NOT NULL,
                created_at DATETIME,
                CONSTRAINT uq_tracked_keyword_workspace UNIQUE (workspace_id, keyword)
            )
        """)
        for row_id, pid, keyword, created_at in con.execute(
            "SELECT id, project_id, keyword, created_at FROM tracked_keywords"
        ).fetchall():
            con.execute(
                "INSERT INTO tracked_keywords_new (id, workspace_id, keyword, created_at) VALUES (?, ?, ?, ?)",
                (row_id, workspace_by_project[pid], keyword, created_at),
            )

        con.execute("""
            CREATE TABLE saved_keywords_new (
                id INTEGER PRIMARY KEY,
                workspace_id INTEGER NOT NULL REFERENCES keyword_workspaces(id),
                keyword VARCHAR NOT NULL,
                volume INTEGER,
                difficulty INTEGER,
                intent VARCHAR,
                created_at DATETIME,
                CONSTRAINT uq_saved_keyword_workspace UNIQUE (workspace_id, keyword)
            )
        """)
        for row_id, pid, keyword, volume, difficulty, intent, created_at in con.execute(
            "SELECT id, project_id, keyword, volume, difficulty, intent, created_at FROM saved_keywords"
        ).fetchall():
            con.execute(
                "INSERT INTO saved_keywords_new (id, workspace_id, keyword, volume, difficulty, intent, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (row_id, workspace_by_project[pid], keyword, volume, difficulty, intent, created_at),
            )

        # keyword_snapshots references tracked_keywords by id and ids are
        # preserved verbatim above, so snapshots need no changes at all.
        con.execute("DROP TABLE tracked_keywords")
        con.execute("ALTER TABLE tracked_keywords_new RENAME TO tracked_keywords")
        con.execute("DROP TABLE saved_keywords")
        con.execute("ALTER TABLE saved_keywords_new RENAME TO saved_keywords")

        # 4. Verify before committing.
        new_tracked = con.execute("SELECT COUNT(*) FROM tracked_keywords").fetchone()[0]
        new_saved = con.execute("SELECT COUNT(*) FROM saved_keywords").fetchone()[0]
        if (new_tracked, new_saved) != (old_tracked, old_saved):
            raise RuntimeError(
                f"Row count mismatch: tracked {old_tracked}->{new_tracked}, "
                f"saved {old_saved}->{new_saved}. Rolling back."
            )

        con.commit()
        print(
            f"Migrated: {len(workspace_by_project)} workspace(s) created, "
            f"{new_tracked} tracked keyword(s) and {new_saved} saved keyword(s) moved."
        )
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB_PATH
    migrate(db_path)
