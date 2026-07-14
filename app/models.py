from datetime import datetime, timezone

def _utcnow():
    return datetime.now(timezone.utc)

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
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
    business_profile = relationship(
        "BusinessProfile", back_populates="project", uselist=False, cascade="all, delete-orphan"
    )


class BusinessProfile(Base):
    """Project-level business knowledge consumed by the AI prompt builder.

    One row per project, loaded fresh at suggestion time (never denormalized onto
    pages), so profile edits affect the next suggestion without a re-crawl.

    services/locations/audiences are entity lists (JSON arrays of strings) rather
    than single scalar fields, since a business can offer multiple services, serve
    multiple locations, and target multiple audiences. See migrations/001_business_profile_entities.py
    for the migration from the earlier flat-column shape.
    """
    __tablename__ = "business_profiles"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, unique=True)

    brand = Column(Text)
    industry = Column(Text)
    services = Column(JSON, default=list)
    locations = Column(JSON, default=list)
    audiences = Column(JSON, default=list)
    tone = Column(Text)
    usp = Column(Text)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    project = relationship("Project", back_populates="business_profile")


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

    markdown = Column(Text)
    fit_markdown = Column(Text)
    internal_links = Column(JSON)

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
    understanding_id = Column(Integer, ForeignKey("page_understanding.id"), nullable=True)
    content = Column(Text, nullable=False)
    rank = Column(Integer, default=0)
    created_at = Column(DateTime, default=_utcnow)

    issue = relationship("Issue", back_populates="suggestions")


class TrackedKeyword(Base):
    """A keyword the user is actively tracking for a project (Overview tab).
    Metrics live in KeywordSnapshot rows, not on this row, so history/trend
    can be computed instead of only ever showing the latest value."""
    __tablename__ = "tracked_keywords"
    __table_args__ = (UniqueConstraint("project_id", "keyword", name="uq_tracked_keyword_project"),)

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    keyword = Column(String, nullable=False)
    created_at = Column(DateTime, default=_utcnow)

    snapshots = relationship(
        "KeywordSnapshot", back_populates="tracked_keyword", cascade="all, delete-orphan"
    )


class KeywordSnapshot(Base):
    """Point-in-time provider metrics for a tracked keyword. Diffing the two
    most recent snapshots (>=7 days apart) is what drives the Trend column --
    a single live API response has nothing to compare against on its own."""
    __tablename__ = "keyword_snapshots"

    id = Column(Integer, primary_key=True)
    tracked_keyword_id = Column(Integer, ForeignKey("tracked_keywords.id"), nullable=False)
    volume = Column(Integer)
    difficulty = Column(Integer)
    intent = Column(String)
    position = Column(Integer)  # SERP rank, populated once rank tracking is wired up; unused for now
    source = Column(String, nullable=False)  # "semrush" | "dataforseo" -- kept for provider cost/usage auditing
    fetched_at = Column(DateTime, default=_utcnow)

    tracked_keyword = relationship("TrackedKeyword", back_populates="snapshots")


class SavedKeyword(Base):
    """User-curated Saved List. Pure curation, not provider-fetched -- metrics
    are copied in at save time and only refreshed if the user asks."""
    __tablename__ = "saved_keywords"
    __table_args__ = (UniqueConstraint("project_id", "keyword", name="uq_saved_keyword_project"),)

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    keyword = Column(String, nullable=False)
    volume = Column(Integer)
    difficulty = Column(Integer)
    intent = Column(String)
    created_at = Column(DateTime, default=_utcnow)


class PageUnderstanding(Base):
    """Cached, LLM-derived understanding of a page (topic, search intent, target
    keyword, which service/location/audience from the business profile it's actually
    relevant to). Tied to a crawl snapshot rather than the page directly, so a new
    crawl naturally invalidates the cache instead of needing an explicit expiry."""
    __tablename__ = "page_understanding"
    __table_args__ = (UniqueConstraint("page_id", "snapshot_id", name="uq_page_understanding_page_snapshot"),)

    id = Column(Integer, primary_key=True)
    page_id = Column(Integer, ForeignKey("pages.id"), nullable=False)
    snapshot_id = Column(Integer, ForeignKey("crawl_snapshots.id"), nullable=False)
    understanding_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
