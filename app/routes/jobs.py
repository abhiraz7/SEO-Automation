"""
Job/Queue routes. GET /projects/{id}/jobs (real Queue drawer data) lands in
Task 2.5; for now this only has the temporary manual-trigger endpoint Task
2.3 asks for, to prove the registry + handler works before the scheduler
(Task 2.4) exists to call it automatically.
"""
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
