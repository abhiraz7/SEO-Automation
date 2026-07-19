"""
Standalone job runner -- executed as a SUBPROCESS by the scheduler's worker
tick:  python -m app.jobs.runner <job_id>

Why a subprocess instead of running handlers in the scheduler thread:
crawl4ai/Playwright hangs intermittently when driven from a non-main thread
on Windows (observed live: a crawl fetched 6 pages then froze forever, and a
hung Python thread cannot be killed, which permanently blocked the
single-worker lane). A subprocess gives every job a clean main thread and --
critically -- something the parent can actually kill on timeout.

The handler owns the job row's status transitions (running/completed/failed)
exactly as before; the parent only intervenes if this process dies or hangs
without finalizing.
"""
import sys

from dotenv import load_dotenv
load_dotenv()

from ..database import SessionLocal  # noqa: E402
from .. import models  # noqa: E402
from .registry import JOB_HANDLERS  # noqa: E402


def main(job_id: int) -> int:
    db = SessionLocal()
    try:
        job = db.get(models.Job, job_id)
        if not job:
            print(f"Job {job_id} not found", file=sys.stderr)
            return 2
        handler = JOB_HANDLERS.get(job.job_type)
        if not handler:
            job.status = "failed"
            job.error = f"No handler registered for job_type={job.job_type!r}"
            db.commit()
            return 3
        handler(db, job)  # never raises; finalizes the job row itself
        db.refresh(job)
        return 0 if job.status == "completed" else 1
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m app.jobs.runner <job_id>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(int(sys.argv[1])))
