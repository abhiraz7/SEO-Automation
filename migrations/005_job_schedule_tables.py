"""
Migration 005: add jobs and schedules tables.

Purely additive -- two new tables, no existing table touched. This is the
generic job/schedule engine that Sprint 2's crawl-settings persistence,
scheduler runner, and Queue drawer (Tasks 2.2-2.5) build on, and that
Phase 4's rank_check/keyword_refresh jobs and Phase 5's backlink_pull job
will reuse without needing their own tables.

jobs: one row per unit of work that ran or is queued to run (crawl,
rank_check, keyword_refresh, ...). Written by whatever creates work
(scheduler tick, or a manual "run now" endpoint) and updated by whichever
handler in app/jobs/handlers/ executes it.

schedules: one row per (project, job_type) recurrence config -- e.g. "crawl
project 3 every 24h". unique on (project_id, job_type) since a project can
only have one active schedule per job type; job-specific settings (crawl
behavior, worker counts, etc.) live in payload rather than as dedicated
columns, since those vary per job_type and this table shouldn't grow a new
column for every future job type.

Run:  python migrations/005_job_schedule_tables.py
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

        if "jobs" not in tables:
            con.execute("""
                CREATE TABLE jobs (
                    id INTEGER PRIMARY KEY,
                    project_id INTEGER NOT NULL REFERENCES projects(id),
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    payload JSON,
                    result_summary JSON,
                    error TEXT,
                    attempts INTEGER DEFAULT 0,
                    scheduled_for DATETIME,
                    started_at DATETIME,
                    finished_at DATETIME,
                    created_at DATETIME
                )
            """)
            con.execute("CREATE INDEX ix_jobs_project_id ON jobs(project_id)")
            con.execute("CREATE INDEX ix_jobs_status ON jobs(status)")
            print("Created jobs table.")
        else:
            print("jobs table already exists -- skipping.")

        if "schedules" not in tables:
            con.execute("""
                CREATE TABLE schedules (
                    id INTEGER PRIMARY KEY,
                    project_id INTEGER NOT NULL REFERENCES projects(id),
                    job_type TEXT NOT NULL,
                    enabled BOOLEAN DEFAULT 1,
                    interval TEXT,
                    cron_expression TEXT,
                    timezone TEXT DEFAULT 'Asia/Kolkata',
                    payload JSON,
                    last_run_at DATETIME,
                    next_run_at DATETIME,
                    created_at DATETIME,
                    UNIQUE (project_id, job_type)
                )
            """)
            con.execute("CREATE INDEX ix_schedules_next_run_at ON schedules(next_run_at)")
            print("Created schedules table.")
        else:
            print("schedules table already exists -- skipping.")

        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
