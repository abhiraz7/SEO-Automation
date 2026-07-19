"""
Pydantic request/response schemas. Currently just BusinessProfile — introduced
alongside its move from flat scalar fields to entity lists, since list-shaped
request bodies are naturally expressed as JSON, not HTML form fields.
"""
from datetime import datetime

from pydantic import BaseModel, Field


class CrawlSettingsIn(BaseModel):
    """Crawler Settings drawer payload. Automation fields (enabled/interval/
    timezone/cron) map onto Schedule's own columns; everything else (crawler
    behavior, worker tuning, verification) is crawl-specific and has no home
    on the generic Schedule table, so it's carried in payload instead --
    other job_types will have their own unrelated payload shapes."""
    # Automation -> Schedule columns
    enabled: bool = False
    interval: str = "24h"  # "24h" | "12h" | "6h" | "weekly" | "cron"
    timezone: str = "Asia/Kolkata"
    cron_expression: str | None = None

    # Crawler behavior -> Schedule.payload
    user_agent: str = "VTechysSEOBot/1.0"
    max_depth: int = 3
    crawl_delay_ms: int = 500
    timeout_s: int = 30
    respect_robots: bool = True
    exclude_patterns: str = "/admin/*"  # newline-separated, kept as raw text (matches the textarea)

    # Workers -> Schedule.payload
    worker_count: int = 3
    concurrency: int = 5
    retry_attempts: int = 2
    worker_timeout_s: int = 30

    # Verification -> Schedule.payload
    firecrawl_validation: bool = False
    coverage_target: int = 98


class CrawlSettingsOut(CrawlSettingsIn):
    id: int
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None


class BusinessProfileIn(BaseModel):
    """Request body for creating/updating a project's business profile."""
    brand: str | None = None
    industry: str | None = None
    services: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    audiences: list[str] = Field(default_factory=list)
    tone: str | None = None
    usp: str | None = None


class BusinessProfileOut(BusinessProfileIn):
    """Response body — adds the identifying fields callers didn't submit."""
    id: int
    project_id: int

    model_config = {"from_attributes": True}


class PageUnderstandingResult(BaseModel):
    """Structured output of app/services/context_builder.py's single Claude call
    per page. Field names match the page_understanding.understanding_json contents."""
    page_type: str
    main_topic: str
    search_intent: str
    primary_keyword: str
    secondary_keywords: list[str] = Field(default_factory=list)
    relevant_service: str | None = None
    relevant_location: str | None = None
    relevant_audience: str | None = None
    geo_relevance: str
    context_confidence: float


class WorthIt(BaseModel):
    """Actionability verdict computed from the metrics (keyword_scoring.py) --
    the number users actually want instead of raw KD. factors is the
    human-readable explanation shown when the score is clicked."""
    score: float                           # 0-10, one decimal
    band: str                              # "easy" | "medium" | "avoid"
    factors: list[str]


class NormalizedKeyword(BaseModel):
    """Single shape both semrush.py and dataforseo.py normalize their
    provider-specific responses into. Nothing above the adapter layer should
    ever branch on which provider answered.

    A lookup has three distinct outcomes and status carries them end-to-end
    (adapter -> provider router -> route -> template):
      ok      -> provider returned real metrics
      no_data -> provider(s) succeeded but have nothing for this keyword+location
      error   -> the lookup itself failed (auth, network, rate limit, ...)
    no_data/error results must never be persisted as KeywordSnapshots -- they'd
    be indistinguishable from a real zero-volume answer and corrupt trend history."""
    keyword: str
    volume: int | None = None
    difficulty: int | None = None          # 0-100
    intent: str | None = None              # informational | navigational | commercial | local
    cpc: float | None = None
    source: str                            # "semrush" | "dataforseo" | "none" (no provider answered)
    fetched_at: datetime
    status: str = "ok"                     # "ok" | "no_data" | "error"
    error: str | None = None               # human-readable reason, only set when status == "error"
    trend_points: list[float] | None = None  # 12 monthly relative-volume points (0-1), oldest first
    worth_it: WorthIt | None = None        # computed by keyword_scoring, attached at the route layer


class KeywordWithTrend(NormalizedKeyword):
    """NormalizedKeyword + a computed (not provider-supplied) trend, for the
    Overview tab table. trend_confidence lets the frontend tell a real
    'stable' apart from the default shown when there's no snapshot history yet."""
    trend: str  # "rising" | "stable" | "falling"
    trend_confidence: str  # "insufficient_data" | "computed"


class WorkspaceIn(BaseModel):
    """Keyword workspaces are the standalone container keyword data hangs off
    (not Project). project_id is an optional link back to a site."""
    name: str
    default_location: str = "US"
    project_id: int | None = None


class WorkspaceOut(WorkspaceIn):
    id: int
    model_config = {"from_attributes": True}


class TrackKeywordIn(BaseModel):
    keyword: str
    location: str = "US"  # ISO country code, see app/keyword_locations.py (DEFAULT_LOCATION)


class SavedKeywordIn(BaseModel):
    keyword: str
    volume: int | None = None
    difficulty: int | None = None
    intent: str | None = None


class SavedKeywordOut(SavedKeywordIn):
    id: int
    model_config = {"from_attributes": True}


class BulkKeywordsIn(BaseModel):
    keywords: list[str] = Field(..., max_length=100)
    location: str = "IN"  # ISO country code, see app/keyword_locations.py
