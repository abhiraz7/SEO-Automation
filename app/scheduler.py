"""
Background scheduler -- three independent APScheduler ticks, wired into the
FastAPI app lifespan (see main.py):

1. dispatch_due_schedules (every 60s): finds enabled Schedule rows where
   next_run_at <= now, creates a Job row for each, advances next_run_at.
2. run_next_crawl_job (every 10s) and 3. run_next_light_job (every 10s) --
   two independent worker lanes. The crawl lane only runs "crawl" jobs; the
   light lane runs everything else (rank_check, keyword_refresh,
   backlink_pull, audit). Each tick only looks at Jobs of its own lane's
   job_types and picks the oldest queued one.
   max_instances=1 per tick means APScheduler won't start a second run of
   that same lane while one is still executing, so each lane processes its
   own jobs one at a time -- no extra locking needed to keep SQLite happy.
   The two lanes run independently, so a slow network-bound crawl no
   longer blocks fast API-based jobs queued behind it (and vice versa).

Runs in-process via BackgroundScheduler (thread-based), not a separate
process or APScheduler's own persistent job store -- our own Job/Schedule
tables ARE the persistence layer; APScheduler here is only the timer that
polls them. This is a single-server design: if the app ever runs as
multiple instances, only one of them should call start().
"""
import logging
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import models
from .database import SessionLocal
from .jobs.registry import JOB_HANDLERS

logger = logging.getLogger("scheduler")

# Hard wall-clock cap per job. Jobs run as killable subprocesses because
# crawl4ai/Playwright can hang forever when driven from a non-main thread on
# Windows (observed live) -- and a hung in-process thread would permanently
# block its worker lane with no way to kill it.
#
# "crawl" is network/browser-bound against a whole site and legitimately can
# take minutes. Everything else in the "light" lane is a handful of API
# calls -- rank_check/keyword_refresh/backlink_pull/audit have no business
# running 15 minutes, so they get a much shorter cap. Otherwise a hung API
# call in the light lane could starve its own lane the same way a slow
# crawl used to starve everything.
CRAWL_JOB_TYPES = {"crawl"}
LIGHT_JOB_TIMEOUT_SECONDS = 180
JOB_TIMEOUT_SECONDS = 900

INTERVAL_TIMEDELTAS = {
    "24h": timedelta(hours=24),
    "12h": timedelta(hours=12),
    "6h": timedelta(hours=6),
    "weekly": timedelta(days=7),
}

_scheduler: BackgroundScheduler | None = None


def compute_next_run_at(schedule: models.Schedule, now: datetime | None = None) -> datetime:
    """cron uses APScheduler's own CronTrigger for the parsing/math rather
    than adding a separate croniter dependency; any other interval value
    (including an unrecognized one) falls back to 24h so a Schedule with a
    typo'd interval still runs eventually instead of never firing again."""
    now = now or datetime.now(timezone.utc)
    if schedule.interval == "cron" and schedule.cron_expression:
        try:
            trigger = CronTrigger.from_crontab(schedule.cron_expression, timezone=schedule.timezone or "UTC")
            nxt = trigger.get_next_fire_time(None, now)
            if nxt:
                return nxt
        except Exception:
            logger.warning(
                "Invalid cron_expression %r on schedule %s -- falling back to 24h",
                schedule.cron_expression, schedule.id,
            )
    return now + INTERVAL_TIMEDELTAS.get(schedule.interval, timedelta(hours=24))


def dispatch_due_schedules() -> None:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        due = (
            db.query(models.Schedule)
            .filter(models.Schedule.enabled == True, models.Schedule.next_run_at <= now)  # noqa: E712
            .all()
        )
        for schedule in due:
            db.add(models.Job(
                project_id=schedule.project_id,
                job_type=schedule.job_type,
                status="queued",
                payload=schedule.payload,
                scheduled_for=schedule.next_run_at,
            ))
            schedule.last_run_at = now
            schedule.next_run_at = compute_next_run_at(schedule, now)
            logger.info(
                "Dispatched %s job for project %s, next run %s",
                schedule.job_type, schedule.project_id, schedule.next_run_at,
            )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("dispatch_due_schedules failed")
    finally:
        db.close()


def _run_next_queued_job_in_lane(job_types: set[str], timeout_seconds: int) -> None:
    """Picks the oldest queued Job whose job_type is in this lane and runs
    it in a subprocess (see JOB_TIMEOUT_SECONDS comment + app/jobs/runner.py
    for why not in-thread). The handler inside the subprocess finalizes the
    job row itself; this parent only cleans up when the subprocess dies or
    times out without finalizing."""
    db = SessionLocal()
    try:
        job = (
            db.query(models.Job)
            .filter(models.Job.status == "queued", models.Job.job_type.in_(job_types))
            .order_by(models.Job.created_at)
            .first()
        )
        if not job:
            return
        job_id = job.id
        logger.info("Running job %s (%s) in subprocess", job_id, job.job_type)
        try:
            result = subprocess.run(
                [sys.executable, "-m", "app.jobs.runner", str(job_id)],
                capture_output=True, text=True, timeout=timeout_seconds,
            )
            stderr_tail = (result.stderr or "")[-500:]
        except subprocess.TimeoutExpired:
            result = None
            stderr_tail = f"killed after exceeding {timeout_seconds}s job timeout"

        # Re-read from a fresh session: the subprocess wrote its own updates.
        db.expire_all()
        job = db.get(models.Job, job_id)
        if job and job.status in ("queued", "running"):
            # Subprocess died/was killed before the handler could finalize.
            job.status = "failed"
            job.error = stderr_tail or (
                f"job subprocess exited with code {result.returncode} without finalizing"
                if result else "job subprocess died without finalizing"
            )
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            logger.warning("Job %s cleaned up as failed: %s", job_id, job.error)
    except Exception:
        db.rollback()
        logger.exception("_run_next_queued_job_in_lane failed")
    finally:
        db.close()


def run_next_crawl_job() -> None:
    _run_next_queued_job_in_lane(CRAWL_JOB_TYPES, JOB_TIMEOUT_SECONDS)


def run_next_light_job() -> None:
    light_job_types = set(JOB_HANDLERS) - CRAWL_JOB_TYPES
    _run_next_queued_job_in_lane(light_job_types, LIGHT_JOB_TIMEOUT_SECONDS)


def _recover_interrupted_jobs() -> None:
    """On startup: anything still marked running belongs to a previous
    process (crashed, killed, or hung-and-restarted) -- mark it failed so it
    doesn't sit as a zombie 'running' row forever and confuse the queue UI."""
    db = SessionLocal()
    try:
        stale = db.query(models.Job).filter(models.Job.status == "running").all()
        for job in stale:
            job.status = "failed"
            job.error = "interrupted: app restarted while this job was running"
            job.finished_at = datetime.now(timezone.utc)
            logger.warning("Recovered interrupted job %s (%s)", job.id, job.job_type)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("_recover_interrupted_jobs failed")
    finally:
        db.close()


def _backfill_next_run_at() -> None:
    """On startup: any Schedule row saved before it had a next_run_at (or
    freshly created without one) gets one computed now, so it isn't
    permanently invisible to dispatch_due_schedules's next_run_at <= now
    filter (NULL never compares true against anything)."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        for schedule in db.query(models.Schedule).filter(models.Schedule.next_run_at.is_(None)).all():
            schedule.next_run_at = compute_next_run_at(schedule, now)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("_backfill_next_run_at failed")
    finally:
        db.close()


def start() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _recover_interrupted_jobs()
    _backfill_next_run_at()
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        dispatch_due_schedules, "interval", seconds=60,
        id="dispatch_due_schedules", max_instances=1, coalesce=True,
    )
    _scheduler.add_job(
        run_next_crawl_job, "interval", seconds=10,
        id="run_next_crawl_job", max_instances=1, coalesce=True,
    )
    _scheduler.add_job(
        run_next_light_job, "interval", seconds=10,
        id="run_next_light_job", max_instances=1, coalesce=True,
    )
    _scheduler.start()
    logger.info(
        "Scheduler started (dispatch every 60s, crawl lane + light lane worker ticks every 10s)."
    )
    return _scheduler


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped.")
