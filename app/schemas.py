"""
Pydantic request/response schemas. Currently just BusinessProfile — introduced
alongside its move from flat scalar fields to entity lists, since list-shaped
request bodies are naturally expressed as JSON, not HTML form fields.
"""
from datetime import datetime
from typing import Any, Literal

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


class BacklinksOverview(BaseModel):
    """Backlinks tab overview (Task 5.1). Same ok/no_data/error discipline as
    NormalizedKeyword -- see backlinks_provider.py.

    spam_score/broken_backlinks/tld_distribution/platform_distribution are
    DataForSEO-only -- verified live against the real backlinks/summary/live
    response (2026-08-09), not guessed. They stay None on the Semrush path,
    which has no equivalent fields -- the UI must treat them as optional,
    not assume every source populates every field."""
    status: str = "ok"  # "ok" | "no_data" | "error"
    error: str | None = None
    authority_score: int | None = None
    referring_domains: int | None = None
    total_backlinks: int | None = None
    follow_links: int | None = None
    nofollow_links: int | None = None
    spam_score: int | None = None
    broken_backlinks: int | None = None
    tld_distribution: dict[str, int] | None = None
    platform_distribution: dict[str, int] | None = None
    source: str = "none"
    fetched_at: datetime


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


class ImageAltSuggestionItem(BaseModel):
    """One structured image_alt suggestion (Image Alt AI hardening pass).
    Replaces the old "1. ...\\n2. ...\\n3. ..." text-list parsing used by every
    other suggestion category (see claude.py/gemini.py.generate_suggestions) --
    image_alt gets real schema validation instead, since a malformed response
    here should be rejected and retried, not silently dropped into an empty
    suggestion list."""
    alt_text: str
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)


class ImageAltSuggestionsResult(BaseModel):
    suggestions: list[ImageAltSuggestionItem]


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
    position: int | None = None  # SERP rank from the rank_check job (Task 4.1); None until that job has run


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


# ── AI Competitor Gap: what the model may return ─────────────────────────────
# The model never writes evidence. It cites evidence IDs (E01, E02, ...) from the list
# the application supplied; the application then fills in each item's label and its
# competitor_count / competitor_total from its OWN data (services/action_plan.py). So
# a wrong or invented count cannot exist in a stored plan, by construction.

ActionType = Literal["add", "expand", "rewrite", "restructure", "leave_unchanged", "separate_page"]
Priority = Literal["high", "medium", "low"]
Confidence = Literal["high", "medium", "low"]


class ModelPlanAction(BaseModel):
    """One action exactly as the model returns it (before validation against the
    supplied evidence)."""
    model_config = {"extra": "ignore"}

    id: str = ""
    type: ActionType
    priority: Priority = "medium"
    title: str = Field(min_length=1, max_length=300)
    problem: str = Field(default="", max_length=1500)
    recommendation: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: Confidence = "low"
    requires_fact_check: bool = False


class ModelActionPlan(BaseModel):
    model_config = {"extra": "ignore"}

    actions: list[ModelPlanAction]


class ModelGapDraft(BaseModel):
    """The atomic draft the model returns for one action."""
    model_config = {"extra": "ignore"}

    draft: str = Field(min_length=1, max_length=6000)
    claims_to_verify: list[str] = Field(default_factory=list)



# ── AI Content Optimizer ─────────────────────────────────────────────────

OptimizerType = Literal[
    "add_section", "expand_section", "rewrite_section", "improve_heading",
    "improve_title", "improve_meta_description", "add_faq", "improve_internal_link",
]


class ModelOptimizerSuggestion(BaseModel):
    """One suggestion exactly as the model returns it. Every field is lenient text:
    the APPLICATION decides what is valid (services/optimizer_plan.resolve_suggestions),
    so a single unsupported or malformed suggestion is discarded -- and shown with its
    reason -- instead of voiding the whole answer."""
    model_config = {"extra": "ignore"}

    type: str = ""
    target: str = ""            # title | meta_description | h1 | sec_NN | new
    priority: str = "medium"
    problem: str = Field(default="", max_length=1500)
    evidence_ids: list[str] = Field(default_factory=list)
    after: str = Field(default="", max_length=6000)
    link_target: str | None = None
    requires_fact_check: bool = False
    claims_to_verify: list[str] = Field(default_factory=list)
    confidence: str = "low"


class ModelOptimizerOutput(BaseModel):
    """The top-level envelope. Each entry of `suggestions` is validated on its own."""
    model_config = {"extra": "ignore"}

    suggestions: list[Any] = Field(default_factory=list)
    no_change_reason: str | None = None


class OptimizerEvidence(BaseModel):
    """One piece of evidence as STORED: rebuilt by the application from its own data,
    never taken from the model."""
    type: str
    label: str
    competitor_count: int = 0
    competitor_total: int = 0
    target_coverage: str | None = None
    gap_id: int | None = None


class OptimizerSuggestion(BaseModel):
    """One suggestion as STORED. Strict: an invalid enum value or an empty required
    field is an error here, never silently stored."""
    id: str
    type: OptimizerType
    target_ref: str
    priority: Priority
    problem: str = Field(min_length=1)
    evidence: list[OptimizerEvidence] = Field(min_length=1)
    before: str | None = None
    after: str = Field(min_length=1)
    link_target: str | None = None
    requires_fact_check: bool = False
    claims_to_verify: list[str] = Field(default_factory=list)
    confidence: Confidence
