from datetime import datetime, timezone

def _utcnow():
    return datetime.now(timezone.utc)

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
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

    # "crawler" (our own crawler+audit.py, Phase 1/2, being retired) |
    # "dataforseo" (DataForSEO on_page API) | "semrush" (reserved -- SEMrush
    # currently only writes to SemrushOnPageSnapshot, not here, until its
    # API units are available again). Lets /onpage-semrush show ONLY
    # non-crawler pages/issues without touching crawler-sourced rows.
    source = Column(String, nullable=False, default="crawler")
    onpage_task_id = Column(Integer, ForeignKey("onpage_tasks.id"), nullable=True)
    word_count = Column(Integer)
    onpage_score = Column(Integer)
    checks = Column(JSON)  # raw DataForSEO checks dict, kept for reference/debugging

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

    # Resolved during crawl (see routes/crawl.py._maybe_resolve_wp_post_id) via
    # WordPress's public core REST API, so deploy doesn't need to ask the user
    # for a numeric post ID every time. Stays NULL if unresolved (no
    # WordPress connection, no matching slug, site unreachable, ...) -- deploy
    # falls back to asking manually in that case.
    wp_post_id = Column(Integer)
    wp_post_type = Column(String)  # e.g. "posts" | "pages" | a custom post type's rest_base -- which WP REST collection it matched

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
    # sha256 of content, normalized (trim/collapse-whitespace/casefold) --
    # see routes/suggestions.py.content_hash(). Backed by a unique index on
    # (issue_id, content_hash) (migration 012) so the same suggestion text
    # can never exist twice for the same issue, whether from two rapid
    # Generate clicks or a regeneration re-producing an already-decided
    # suggestion's wording.
    content_hash = Column(String)
    rank = Column(Integer, default=0)
    # Acceptance tracking (V6): what the user decided about this suggestion.
    # This status trail is the raw material for the future learning dataset --
    # regeneration must never delete accepted/edited/deployed rows.
    status = Column(String, nullable=False, default="pending")  # pending|accepted|rejected|edited|deployed
    edited_content = Column(Text)          # user's modified version, when status == "edited"
    accepted_at = Column(DateTime)
    deployed_at = Column(DateTime)
    created_at = Column(DateTime, default=_utcnow)

    issue = relationship("Issue", back_populates="suggestions")

    __table_args__ = (
        UniqueConstraint("issue_id", "content_hash", name="uq_suggestion_issue_content_hash"),
    )


class KeywordWorkspace(Base):
    """Standalone keyword-research container. Keyword Research no longer hangs
    off Project -- a workspace can exist with no project at all (pure research
    before a site exists) or link to one via the nullable project_id, keeping
    the nullability on this one narrow join point instead of scattered across
    every TrackedKeyword/SavedKeyword row."""
    __tablename__ = "keyword_workspaces"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)                # e.g. "VTechys India", "Client X"
    default_location = Column(String, default="US")      # ISO code, see app/keyword_locations.py (DEFAULT_LOCATION)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    tracked_keywords = relationship(
        "TrackedKeyword", back_populates="workspace", cascade="all, delete-orphan"
    )
    saved_keywords = relationship(
        "SavedKeyword", back_populates="workspace", cascade="all, delete-orphan"
    )


class TrackedKeyword(Base):
    """A keyword the user is actively tracking in a workspace (Overview tab).
    Metrics live in KeywordSnapshot rows, not on this row, so history/trend
    can be computed instead of only ever showing the latest value."""
    __tablename__ = "tracked_keywords"
    __table_args__ = (UniqueConstraint("workspace_id", "keyword", name="uq_tracked_keyword_workspace"),)

    id = Column(Integer, primary_key=True)
    workspace_id = Column(Integer, ForeignKey("keyword_workspaces.id"), nullable=False)
    keyword = Column(String, nullable=False)
    # Market this keyword was tracked against, persisted so refresh jobs
    # (keyword_refresh/rank_check) re-query the SAME market the user chose
    # instead of whatever the app-wide default happens to be at refresh time.
    location = Column(String, nullable=False, default="US")
    created_at = Column(DateTime, default=_utcnow)

    workspace = relationship("KeywordWorkspace", back_populates="tracked_keywords")
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
    trend_points = Column(String)  # provider 12-month trend series as "1.00,0.82,..." -- drives the sparkline
    source = Column(String, nullable=False)  # "semrush" | "dataforseo" -- kept for provider cost/usage auditing
    fetched_at = Column(DateTime, default=_utcnow)

    tracked_keyword = relationship("TrackedKeyword", back_populates="snapshots")


class SavedKeyword(Base):
    """User-curated Saved List. Pure curation, not provider-fetched -- metrics
    are copied in at save time and only refreshed if the user asks."""
    __tablename__ = "saved_keywords"
    __table_args__ = (UniqueConstraint("workspace_id", "keyword", name="uq_saved_keyword_workspace"),)

    id = Column(Integer, primary_key=True)
    workspace_id = Column(Integer, ForeignKey("keyword_workspaces.id"), nullable=False)
    keyword = Column(String, nullable=False)
    volume = Column(Integer)
    difficulty = Column(Integer)
    intent = Column(String)
    created_at = Column(DateTime, default=_utcnow)

    workspace = relationship("KeywordWorkspace", back_populates="saved_keywords")


class WordPressConnection(Base):
    """Connection to a project's WordPress site via the claude-wp-mcp plugin
    (POST {site_url}/wp-json/cwpm/v1/tool, Bearer auth). api_token is Fernet-
    encrypted at rest (app/wordpress.py owns encrypt/decrypt; nothing else
    should touch the raw token). is_staging defaults True on purpose: deploys
    go to staging until someone deliberately flips a connection to live."""
    __tablename__ = "wordpress_connections"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, unique=True)
    site_url = Column(String, nullable=False)
    api_token = Column(Text, nullable=False)  # Fernet-encrypted, never plaintext
    is_staging = Column(Boolean, default=True)
    last_verified_at = Column(DateTime)
    last_verify_ok = Column(Boolean)
    created_at = Column(DateTime, default=_utcnow)


class SuggestionRevision(Base):
    """One deploy (or rollback) of a suggestion to WordPress. before_value is
    fetched live from WordPress right before writing -- not assumed from our
    own Page row, since the live site is the source of truth and may have
    drifted since our last crawl. A revision is only written on a
    successful deploy; failed deploys leave no row (see routes.wordpress
    docstrings) so 'has a revision' always means 'really happened'."""
    __tablename__ = "suggestion_revisions"

    id = Column(Integer, primary_key=True)
    suggestion_id = Column(Integer, ForeignKey("suggestions.id"), nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    field_name = Column(String, nullable=False)  # matches Issue.category -- see routes/wordpress.py FIELD_DEPLOYERS
    before_value = Column(Text)
    after_value = Column(Text)
    wp_post_id = Column(Integer, nullable=False)
    deployed_via = Column(String, nullable=False)  # tool name called on the plugin, e.g. "yoast_set_meta"
    deployed_at = Column(DateTime, default=_utcnow)
    rolled_back_at = Column(DateTime)
    deploy_result_raw = Column(JSON)

    suggestion = relationship("Suggestion")


class BacklinkSnapshot(Base):
    """Point-in-time backlinks_overview pull for a project (Task 5.1). One
    row per fetch, same pattern as KeywordSnapshot -- history lets a future
    diff show 'authority score went from 42 to 46' instead of only ever the
    latest number."""
    __tablename__ = "backlink_snapshots"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    authority_score = Column(Integer)
    referring_domains = Column(Integer)
    total_backlinks = Column(Integer)
    follow_links = Column(Integer)
    nofollow_links = Column(Integer)
    # DataForSEO-only (migration 020) -- NULL on Semrush-sourced rows, which
    # have no equivalent fields.
    spam_score = Column(Integer)
    broken_backlinks = Column(Integer)
    tld_distribution = Column(JSON)
    platform_distribution = Column(JSON)
    source = Column(String, nullable=False)
    fetched_at = Column(DateTime, default=_utcnow)


class SemrushOnPageSnapshot(Base):
    """Cached on-page issue counts for one SEMrush Projects-API project
    (semrush_project_id -- NOT a FK to our own `projects` table; the
    /onpage-semrush listing is sourced entirely from SEMrush's own
    list_projects(), independent of our app's Project rows). One row per
    SEMrush project, upserted on each manual refresh -- GET /onpage-semrush
    reads only from here, never calls SEMrush live, so viewing the page
    costs zero API units. Only the explicit "Refresh from SEMrush" action
    (app/semrush_audit.py's fetch_onpage_issue_counts, billed per issue
    type per project) writes new values here."""
    __tablename__ = "semrush_onpage_snapshots"

    id = Column(Integer, primary_key=True)
    semrush_project_id = Column(Integer, nullable=False, unique=True)
    name = Column(String)
    url = Column(String)
    total = Column(Integer, default=0)
    critical = Column(Integer, default=0)
    warnings = Column(Integer, default=0)
    by_category = Column(JSON, default=dict)
    issues_detail = Column(JSON, default=list)  # [{category, severity, message, url}, ...] -- powers the "Issues by category" accordion
    error = Column(Text)
    fetched_at = Column(DateTime, default=_utcnow)


class BacklinkRecord(Base):
    """One tracked backlink (Task 5.2's new/lost diffing target). Semrush's
    backlinks_overview only gives aggregate counts -- populating this table
    needs the separate per-link 'backlinks' report type, which
    jobs/handlers/backlink_pull.py (Task 5.2) is responsible for calling."""
    __tablename__ = "backlink_records"
    __table_args__ = (UniqueConstraint("project_id", "source_url", "target_url", name="uq_backlink_record"),)

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    source_url = Column(Text, nullable=False)
    target_url = Column(Text, nullable=False)
    anchor_text = Column(Text)
    # DataForSEO-only (migration 021) -- NULL on Semrush-sourced rows.
    domain_rank = Column(Integer)
    spam_score = Column(Integer)
    is_follow = Column(Boolean)
    first_seen_at = Column(DateTime, default=_utcnow)
    last_seen_at = Column(DateTime, default=_utcnow)
    lost_at = Column(DateTime)  # set when a pull no longer finds this link -- NULL means still live


class OnPageTask(Base):
    """One DataForSEO on_page/task_post site-wide crawl (DataForSEO runs the
    crawl on their end -- we just poll tasks_ready and pull results). A page
    fetched via the synchronous instant_pages check instead has
    onpage_task_id = NULL."""
    __tablename__ = "onpage_tasks"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    dataforseo_task_id = Column(String, nullable=False)
    max_crawl_pages = Column(Integer, default=20)
    status = Column(String, nullable=False, default="posted")  # posted|fetched|error
    pages_crawled = Column(Integer)
    error = Column(Text)
    finished_at = Column(DateTime)
    created_at = Column(DateTime, default=_utcnow)


class PageLink(Base):
    """One row per link found by DataForSEO's on_page/links endpoint during
    a site-wide crawl task (OnPageTask). Wholesale-replaced per task, same
    "stale data must not linger" discipline as Issue rows in
    onpage_semrush.py._store_page_result -- a link DataForSEO no longer
    reports (fixed, removed) must disappear from here too, not persist as
    a false positive.

    url_from/url_to are absolute URLs (DataForSEO's link_from/link_to),
    not FKs to Page -- link_to often points at a URL DataForSEO's crawl
    never visited as a page in its own right (e.g. a PDF, an external
    domain, a URL beyond max_crawl_pages), so it can't always resolve to
    a Page row."""
    __tablename__ = "page_links"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    onpage_task_id = Column(Integer, ForeignKey("onpage_tasks.id"), nullable=False)

    url_from = Column(Text, nullable=False)
    url_to = Column(Text, nullable=False)
    link_type = Column(String)  # anchor | image | canonical | meta | alternate | redirect | link
    direction = Column(String)  # internal | external
    dofollow = Column(Boolean)
    is_broken = Column(Boolean, default=False)
    status_code = Column(Integer)
    anchor_text = Column(Text)
    is_link_relation_conflict = Column(Boolean, default=False)

    created_at = Column(DateTime, default=_utcnow)


class CrawlerSettings(Base):
    """Singleton row (id=1) -- master switch for the retiring custom
    crawler+audit.py pipeline. Gates: routes/crawl.py's /crawl and
    /crawl-single, routes/jobs.py's test-crawl and run-now(crawl),
    scheduler.py's dispatch_due_schedules (skips enqueueing new crawl jobs
    while disabled), and hides the Crawl/Crawl Site buttons in
    project_detail.html. Deliberately not surfaced in the sidebar -- only
    reachable at /settings/crawler by typing the URL directly, since this
    is an internal kill switch, not a normal-user-facing setting."""
    __tablename__ = "crawler_settings"

    id = Column(Integer, primary_key=True)
    enabled = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class SiteAuditSettings(Base):
    """Singleton row (id=1) -- the minimum gap (hours) required between two
    completed DataForSEO Site Audit crawls for the same project, editable
    from /settings. Exists because start_site_audit has no cost/dedup
    protection of its own: without this, a double-clicked "Refresh now" (or
    someone re-running it minutes apart out of impatience) bills DataForSEO
    for a full re-crawl of pages that almost certainly haven't changed.
    Deliberately separate from CrawlerSettings -- this gates the DataForSEO
    on-page flow, not the retiring custom crawler."""
    __tablename__ = "site_audit_settings"

    id = Column(Integer, primary_key=True)
    cooldown_hours = Column(Integer, nullable=False, default=24)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class ProviderSetting(Base):
    """Which on-page data provider is active for /onpage-semrush --
    "dataforseo" or "semrush". Exactly one row has enabled=True at a time
    (radio-style, not independent checkboxes), enforced in
    routes/settings.py rather than a DB constraint since SQLite has no
    partial-unique-index shorthand for "at most one true"."""
    __tablename__ = "provider_settings"

    provider = Column(String, primary_key=True)  # "dataforseo" | "semrush"
    enabled = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class VisibilityCheck(Base):
    """One DataForSEO live SERP lookup (/serp/google/organic/live/advanced,
    via dataforseo.fetch_serp) run for a project, used to see whether the
    project's own domain shows up in Google's real AI Overview citations
    and/or organic rankings for a query -- all fields here are values
    DataForSEO's API actually returned, nothing derived or invented."""
    __tablename__ = "visibility_checks"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    query = Column(String, nullable=False)
    ai_overview_present = Column(Boolean, nullable=False, default=False)
    brand_in_ai_overview = Column(Boolean, nullable=False, default=False)
    ai_overview_references = Column(JSON)  # [{domain, source, url, title}, ...] straight from DataForSEO
    organic_rank = Column(Integer)  # project's own domain's rank_absolute, null if not ranking
    competitor_domains = Column(JSON)  # other domains seen (AI Overview refs + top organic), straight from the API
    raw_error = Column(Text)  # DataForSEO error string, if the call failed
    created_at = Column(DateTime, default=_utcnow)


class Job(Base):
    """A unit of scheduled or on-demand background work (crawl, rank_check,
    keyword_refresh, ...). Handlers are looked up by job_type in
    app/jobs/registry.py; this table only records what ran and its outcome --
    it has no opinion on what a job actually does."""
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    job_type = Column(String, nullable=False)  # "crawl" | "rank_check" | "keyword_refresh" | ...
    status = Column(String, nullable=False, default="queued")  # queued|running|completed|failed|cancelled
    payload = Column(JSON)
    result_summary = Column(JSON)
    error = Column(Text)
    attempts = Column(Integer, default=0)
    scheduled_for = Column(DateTime)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    created_at = Column(DateTime, default=_utcnow)


class Schedule(Base):
    """Recurring-job configuration for one project+job_type pair. The
    scheduler polls enabled rows where next_run_at <= now, creates a Job from
    each, and advances next_run_at -- this table only holds *when/how often*;
    job-specific settings (crawl behavior, etc.) live in payload."""
    __tablename__ = "schedules"
    __table_args__ = (UniqueConstraint("project_id", "job_type", name="uq_schedule_project_job_type"),)

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    job_type = Column(String, nullable=False)
    enabled = Column(Boolean, default=True)
    interval = Column(String)  # "24h" | "12h" | "6h" | "weekly" | "cron"
    cron_expression = Column(String)
    timezone = Column(String, default="Asia/Kolkata")
    payload = Column(JSON)
    last_run_at = Column(DateTime)
    next_run_at = Column(DateTime)
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


class Competitor(Base):
    """Competitor Analysis module foundation. ONE competitor record per
    (project, domain) -- Keyword Gap, Backlink Gap, SERP Comparison, and
    every other future Competitor Analysis branch must query this same
    table rather than each growing their own competitor list. domain is
    always the normalized form (domain_utils.normalize_domain) so
    example.com / http://example.com / www.example.com/ can never become
    three separate rows for the same real competitor."""
    __tablename__ = "competitors"
    __table_args__ = (UniqueConstraint("project_id", "domain", name="uq_competitor_project_domain"),)

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    domain = Column(String, nullable=False)
    display_name = Column(String)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class CompetitorSnapshot(Base):
    """Point-in-time comparison metrics -- same pattern as BacklinkSnapshot/
    KeywordSnapshot: one row per fetch, never overwritten, so history is
    preserved. competitor_id NULL means this snapshot is the PROJECT'S OWN
    site (the "My Site" column in the comparison table), not a competitor
    -- keeps one table serving both instead of duplicating columns.

    Fetched only on an explicit Refresh action (never on page load, per
    the Competitor Analysis brief's "do not add expensive API calls on
    every page load" rule) via app/services/competitor_analysis.py, which
    reads existing provider adapters (semrush.py, backlinks_provider.py)
    -- no new provider integration was added for this module."""
    __tablename__ = "competitor_snapshots"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    competitor_id = Column(Integer, ForeignKey("competitors.id"), nullable=True)
    organic_keywords = Column(Integer)
    referring_domains = Column(Integer)
    total_backlinks = Column(Integer)
    keywords_source = Column(String)   # which provider organic_keywords came from
    backlinks_source = Column(String)  # which provider referring_domains/total_backlinks came from
    error = Column(Text)               # set (fields above stay NULL) if every provider call failed
    fetched_at = Column(DateTime, default=_utcnow)
