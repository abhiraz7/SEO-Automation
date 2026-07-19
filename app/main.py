from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI

from . import scheduler as job_scheduler
from .database import Base, engine
from .routes import audit, crawl, jobs, keywords, projects, suggestions, wordpress

Base.metadata.create_all(bind=engine)


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
