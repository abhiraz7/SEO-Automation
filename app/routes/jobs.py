"""
Job/Queue routes. GET /projects/{id}/jobs (real Queue drawer data) lands in
Task 2.5; for now this only has the temporary manual-trigger endpoint Task
2.3 asks for, to prove the registry + handler works before the scheduler
(Task 2.4) exists to call it automatically, plus a dev-only endpoint to
force a Schedule to fire on the next scheduler tick for testing.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..jobs.registry import JOB_HANDLERS

router = APIRouter()


@router.post("/projects/{project_id}/jobs/test-crawl")
def test_crawl_job(project_id: int, db: Session = Depends(get_db)):
    """Temporary dev endpoint: create a crawl Job and run it synchronously
    right here, so Task 2.3 can be verified without the scheduler (Task 2.4)
    existing yet. Not meant to survive past this phase as the real trigger
    path -- Task 2.4's scheduler and Task 2.5's Queue drawer supersede it."""
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    job = models.Job(project_id=project_id, job_type="crawl", status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)

    JOB_HANDLERS["crawl"](db, job)
    db.refresh(job)

    return {
        "id": job.id,
        "status": job.status,
        "result_summary": job.result_summary,
        "error": job.error,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


@router.get("/projects/{project_id}/jobs")
def list_jobs(project_id: int, db: Session = Depends(get_db)):
    """Queue drawer data: all running+queued jobs, latest 20 completed and
    failed each, plus the badge count (queued+running). One endpoint rather
    than one per tab so the 10s poll is a single request."""
    if not db.get(models.Project, project_id):
        raise HTTPException(status_code=404, detail="Project not found")

    def _rows(query):
        return [
            {
                "id": j.id,
                "job_type": j.job_type,
                "status": j.status,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "finished_at": j.finished_at.isoformat() if j.finished_at else None,
                "result_summary": j.result_summary,
                "error": j.error,
            }
            for j in query
        ]

    base = db.query(models.Job).filter(models.Job.project_id == project_id)
    running = _rows(base.filter(models.Job.status == "running").order_by(models.Job.started_at.desc()))
    queued = _rows(base.filter(models.Job.status == "queued").order_by(models.Job.created_at))
    completed = _rows(base.filter(models.Job.status == "completed").order_by(models.Job.finished_at.desc()).limit(20))
    failed = _rows(base.filter(models.Job.status.in_(["failed", "cancelled"])).order_by(models.Job.finished_at.desc()).limit(20))

    return {
        "running": running,
        "queued": queued,
        "completed": completed,
        "failed": failed,
        "badge": len(running) + len(queued),
    }


@router.post("/projects/{project_id}/schedules/{job_type}/run-now")
def force_schedule_run_now(project_id: int, job_type: str, db: Session = Depends(get_db)):
    """Dev/test-only: force a Schedule to look due so the next scheduler
    dispatch tick (within 60s) picks it up, without waiting a real day/week
    for its interval to elapse. Enables the schedule too, since a disabled
    one would never be picked up regardless of next_run_at."""
    schedule = (
        db.query(models.Schedule)
        .filter(models.Schedule.project_id == project_id, models.Schedule.job_type == job_type)
        .first()
    )
    if not schedule:
        raise HTTPException(status_code=404, detail=f"No {job_type!r} schedule for project {project_id}")
    schedule.enabled = True
    schedule.next_run_at = datetime.now(timezone.utc)
    db.commit()
    return {"schedule_id": schedule.id, "next_run_at": schedule.next_run_at}
