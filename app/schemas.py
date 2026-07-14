"""
Pydantic request/response schemas. Currently just BusinessProfile — introduced
alongside its move from flat scalar fields to entity lists, since list-shaped
request bodies are naturally expressed as JSON, not HTML form fields.
"""
from datetime import datetime

from pydantic import BaseModel, Field


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


class KeywordWithTrend(NormalizedKeyword):
    """NormalizedKeyword + a computed (not provider-supplied) trend, for the
    Overview tab table. trend_confidence lets the frontend tell a real
    'stable' apart from the default shown when there's no snapshot history yet."""
    trend: str  # "rising" | "stable" | "falling"
    trend_confidence: str  # "insufficient_data" | "computed"


class TrackKeywordIn(BaseModel):
    keyword: str
    location: str = "IN"  # ISO country code, see app/keyword_locations.py


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
