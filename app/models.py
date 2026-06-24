from datetime import datetime, timezone

def _utcnow():
    return datetime.now(timezone.utc)

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship

from .database import Base


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    base_url = Column(String, nullable=False)
    project_type = Column(String, default="manual")  # manual | connected
    created_at = Column(DateTime, default=_utcnow)

    pages = relationship("Page", back_populates="project", cascade="all, delete-orphan")


class Page(Base):
    __tablename__ = "pages"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    url = Column(String, nullable=False)

    status_code = Column(Integer)
    error = Column(Text)

    title = Column(Text)
    meta_description = Column(Text)
    meta_keywords = Column(Text)

    h1 = Column(JSON)
    h2 = Column(JSON)
    heading_structure = Column(JSON)
    image_alts = Column(JSON)

    domain_schema = Column(JSON)
    page_schemas = Column(JSON)

    canonical = Column(Text)

    og_title = Column(Text)
    og_description = Column(Text)
    og_url = Column(Text)

    twitter_title = Column(Text)
    twitter_description = Column(Text)
    twitter_site = Column(Text)
    twitter_card = Column(Text)

    lang = Column(Text)
    custom_content = Column(Text)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    project = relationship("Project", back_populates="pages")
    snapshots = relationship("CrawlSnapshot", back_populates="page", cascade="all, delete-orphan")
    issues = relationship("Issue", back_populates="page", cascade="all, delete-orphan")


class CrawlSnapshot(Base):
    __tablename__ = "crawl_snapshots"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    page_id = Column(Integer, ForeignKey("pages.id"), nullable=False)
    url = Column(String, nullable=False)
    data = Column(JSON)
    crawled_at = Column(DateTime, default=_utcnow)

    page = relationship("Page", back_populates="snapshots")


class Issue(Base):
    __tablename__ = "issues"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    page_id = Column(Integer, ForeignKey("pages.id"), nullable=False)
    category = Column(String, nullable=False)  # title, meta_description, h1, h2, image_alt, schema, canonical, opengraph, twitter, lang, content
    rule = Column(String, nullable=False)  # missing, too_short, too_long, multiple, duplicate, poor_structure, empty, invalid, thin
    severity = Column(String, default="warning")  # error | warning
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_utcnow)

    page = relationship("Page", back_populates="issues")
    suggestions = relationship("Suggestion", back_populates="issue", cascade="all, delete-orphan")


class Suggestion(Base):
    __tablename__ = "suggestions"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    page_id = Column(Integer, ForeignKey("pages.id"), nullable=False)
    issue_id = Column(Integer, ForeignKey("issues.id"), nullable=False)
    content = Column(Text, nullable=False)
    rank = Column(Integer, default=0)
    created_at = Column(DateTime, default=_utcnow)

    issue = relationship("Issue", back_populates="suggestions")
