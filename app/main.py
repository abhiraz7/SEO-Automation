from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI

from . import build_info, logging_setup, schema_check, scheduler as job_scheduler
from .database import Base, engine
from .routes import audit, competitors, crawl, jobs, keywords, links, onpage_semrush, optimizer, projects, search_console, security, settings, suggestions, visibility, wordpress

logging_setup.configure_logging()   # timestamps + levels on every WARNING and above, before anything below can log
Base.metadata.create_all(bind=engine)
schema_check.log_drift_at_startup(engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    job_scheduler.start()
    yield
    job_scheduler.shutdown()


app = FastAPI(
    title="VTechSEO",
    description="SEO Automation Platform with AI-powered crawling and analysis",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(projects.router)
app.include_router(crawl.router)
app.include_router(audit.router)
app.include_router(suggestions.router)
app.include_router(keywords.router)
app.include_router(jobs.router)
app.include_router(wordpress.router)
app.include_router(onpage_semrush.router)
app.include_router(settings.router)
app.include_router(security.router)
app.include_router(visibility.router)
app.include_router(competitors.router)
app.include_router(optimizer.router)
app.include_router(links.router)
app.include_router(search_console.router)


@app.get("/version")
def version():
    """Which commit this process is running (see build_info.py) and whether the
    database has every column the code expects (see schema_check.py). No auth and
    no writes -- safe for the deploy pipeline to poll, and to fail a deploy on
    schema.ok being false."""
    return {**build_info.get_build_info(), "schema": schema_check.schema_report(engine)}
