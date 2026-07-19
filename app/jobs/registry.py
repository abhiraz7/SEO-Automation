"""
Maps job_type strings to the handler function that executes them. Both the
scheduler tick (Task 2.4) and the test-crawl endpoint (Task 2.3) go through
this one dict rather than special-casing job types inline, so adding a new
job type (rank_check, keyword_refresh, backlink_pull, ...) is one entry here
plus a handler module -- nothing else changes.

Every handler has the signature (db: Session, job: models.Job) -> None and
must never raise -- see each handler's docstring for why.
"""
from .handlers.crawl import run_crawl_job
from .handlers.keyword_refresh import run_keyword_refresh_job
from .handlers.rank_check import run_rank_check_job

JOB_HANDLERS = {
    "crawl": run_crawl_job,
    "rank_check": run_rank_check_job,
    "keyword_refresh": run_keyword_refresh_job,
}
