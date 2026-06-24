from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI

from .database import Base, engine
from .routes import audit, crawl, projects, suggestions

Base.metadata.create_all(bind=engine)

app = FastAPI(title="VTechSEO")

app.include_router(projects.router)
app.include_router(crawl.router)
app.include_router(audit.router)
app.include_router(suggestions.router)
