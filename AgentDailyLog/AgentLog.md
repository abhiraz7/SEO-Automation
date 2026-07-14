# AgentLog — VTechSEO

---

## 2026-06-24 — Session: Progress Snapshot

### Status

**Phase 1 — Crawl Engine (V1)**: COMPLETE
- `app/crawler.py` — single page + full site crawl
- `app/models.py` — Project, Page, CrawlSnapshot tables
- `app/routes/crawl.py` — crawl routes
- `app/routes/projects.py` — project CRUD
- `app/database.py` — SQLite + SQLAlchemy setup
- `app/main.py` — FastAPI app entry
- Templates: base.html, index.html, project_detail.html, page_detail.html, partials/sidebar.html, partials/pages_table.html

**Phase 2 — SEO Audit (V1.5)**: COMPLETE
- `app/audit.py` — rules for: title, meta_description, h1, h2, image_alt, schema, canonical, opengraph, twitter, lang, content (thin)
- `app/models.py` — Issue table (category, rule, severity, message)
- `app/routes/audit.py` — audit routes

### Not Started
- Phase 3 — AI Suggestions (Claude API, generate 5 per issue, store, display)
- Phase 4 — Rule Validation
- Phase 5 — LLM Judge
- Phase 6 — Acceptance Tracking
- Phase 7 — Learning Dataset (Supabase)
- Phase 8 — RivalFlow
- Phase 9 — RAG
- Phase 10 — AI Visibility Prediction
- Phase 11 — WordPress Deploy

### Next
Start Phase 3: Add `Suggestion` model → Claude API integration → generate 5 suggestions per issue → store → display on page_detail.

---

## 2026-06-24 — Session: Revert to SQLite + Supabase stub layer

### What Changed
- `app/database.py` — reverted to `sqlite:///seo_automation.db`; marked the exact line to swap for PostgreSQL when ready
- `app/supabase_client.py` — new: Supabase integration stub for learning datasets (`acceptance_dataset`, `judge_dataset`, `visibility_dataset`, `memory_dataset`); all functions are silent no-ops until `SUPABASE_URL` + `SUPABASE_KEY` env vars are set
- `requirements.txt` — `psycopg2-binary` kept (harmless, needed for future PostgreSQL migration)

### Why
Supabase `t4g.nano` exposes only an IPv6 direct connection; this Windows machine has no IPv6 internet. The IPv4 pooler (`aws-0-ap-northeast-1.pooler.supabase.com`) rejected the project tenant. Blocked at network level, not a code issue. Will migrate after core product is stable.

### Status
- Phase 1 Crawl Engine: COMPLETE
- Phase 2 SEO Audit: COMPLETE
- Phase 3 AI Suggestions: NEXT

### Next
Add `Suggestion` model to `models.py` → `app/claude.py` for Claude API → generate 5 suggestions per issue → store → display on `page_detail.html`

---

## 2026-06-25 — Session: Phase 3 — AI Suggestions

### What Changed
- `app/models.py` — added `Suggestion` table (project_id, page_id, issue_id, content, rank); added `suggestions` relationship to `Issue`; fixed `datetime.utcnow` deprecation → `_utcnow()` using `timezone.utc`
- `app/claude.py` — new: Claude API integration using `claude-haiku-4-5-20251001`; lazy client init; generates exactly 5 ranked suggestions per issue
- `app/routes/suggestions.py` — new: POST `/projects/{project_id}/pages/{page_id}/issues/{issue_id}/suggest`; clears old suggestions, calls Claude, stores 5 new ones, redirects back
- `app/main.py` — wired `suggestions.router`; added `load_dotenv()` on startup
- `app/templates/page_detail.html` — issues table replaced with expandable issue cards; each card has "Get Suggestions" button; ranked suggestions display inline below the issue
- `requirements.txt` — added `anthropic`, `python-dotenv`

### Status
- Phase 1 Crawl Engine: COMPLETE
- Phase 2 SEO Audit: COMPLETE
- Phase 3 AI Suggestions: COMPLETE
- Phase 4 Rule Validation: NEXT

### Next
Add `app/validator.py` — validate each suggestion against length, keyword presence, uniqueness, readability rules before storing; show pass/fail badges on suggestion cards in `page_detail.html`

---

## 2026-06-25 — Session: UI Redesign (Light Theme + Project List)

### What Changed
- `app/templates/base.html` — full rewrite: light theme (`#f8fafc` bg, `#fff` surface, `#e2e8f0` borders); removed dark CSS vars, user avatar, notification bell; top nav is clean 52px bar with only page-specific topbar slot
- `app/templates/partials/sidebar.html` — full rewrite: 210px white sidebar with icon+label nav items, section headers (SEO Strategy / AI / Tools / Other), indigo active state; removed usage/credits footer
- `app/templates/index.html` — replaced card grid with simple list view; each row shows favicon, name, URL, page count, issue count, date, Open + Delete buttons; New Project form hidden by default, toggled via button click
- `app/templates/project_detail.html` — light theme topbar with breadcrumb, Crawl Site + Run Audit buttons
- `app/templates/page_detail.html` — light theme: workflow stepper, 6 KPI cards, 6-col optimization workspace (Current / AI Suggestions / Editor / Rule Validation / LLM Judge / AI Visibility), issue table
- `app/templates/partials/pages_table.html` — light theme white table
- `app/routes/projects.py` — added `POST /projects/{project_id}/delete`; cascade delete via existing SQLAlchemy relationships (pages → issues → suggestions)

### Status
- Phase 1 Crawl Engine: COMPLETE
- Phase 2 SEO Audit: COMPLETE
- Phase 3 AI Suggestions: COMPLETE
- UI Redesign (Light Theme): COMPLETE
- Phase 4 Rule Validation: NEXT

### Next
Add `app/validator.py` — validate each suggestion against length, keyword presence, uniqueness, readability rules before storing; show pass/fail badges on suggestion cards in `page_detail.html`

---

## 2026-06-25 — Session: Project Detail Page Redesign + Semrush Panel

### What Changed
- `app/templates/project_detail.html` — full rewrite to match enterprise audit workspace screenshot:
  - **Topbar**: breadcrumb + Active badge + Crawl Site + Run Audit buttons (htmx-wired, unchanged behaviour)
  - **Workflow Stepper**: 6-step progress bar (Crawl → Audit → AI Fix → Validate → Judge → Deploy) with live state derived from project data
  - **KPI Cards** (6-col grid): Site Health score gauge, Pages Crawled, Total Issues, Critical Errors, Warnings, Semrush API stub card (click-scrolls to integration panel)
  - **Semrush Integration Panel**: 4 metric placeholders (Domain Authority, Organic Traffic, Ranking Keywords, Referring Domains) gated on `SEMRUSH_API_KEY`; callout explains exactly how Semrush data powers AI Suggestions
  - **Pages Table**: title/meta char counts, critical vs total issue badge split, Semrush KW column stub, enterprise header styling

### How Semrush API Helps
- **Per-page keyword rankings** → Claude uses actual target keyword when generating meta title/description suggestions
- **Competitor gap analysis** → surfaces pages competitors rank for that this site doesn't
- **Backlink toxicity per page** → feeds into LLM Judge scoring
- **SERP feature opportunities** → Featured Snippet / PAA optimizations per page
- Add `SEMRUSH_API_KEY` to `.env` to activate (endpoint: `api.semrush.com`)

### Status
- Phase 1 Crawl Engine: COMPLETE
- Phase 2 SEO Audit: COMPLETE
- Phase 3 AI Suggestions: COMPLETE
- UI Redesign (Light Theme): COMPLETE
- Project Detail Redesign (Screenshot UI + Semrush Panel): COMPLETE
- Phase 4 Rule Validation: NEXT

### Next
Add `app/validator.py` — validate each suggestion against length, keyword presence, uniqueness, readability rules before storing; show pass/fail badges on suggestion cards in `page_detail.html`

---


## 2026-06-25 — Session: Optimization Workspace on Project Page

### What Changed
- `.env` — removed leading space from `SEMRUSH_API_KEY`
- `app/routes/projects.py` — `project_detail()` now builds `page_data` per page: issues + suggestions from DB, grouped by `issue_id`, `title_checklist`, char counts
- `app/templates/project_detail.html` — replaced pages table with Optimization Workspace (6-col card per page):
  - Current (Live): Meta Title + char badge, Meta Desc + char badge, H1, Canonical
  - AI Suggestions (Claude): ranked title suggestions or "No suggestions yet" + Get AI Fix link
  - Editor: Phase 5 stub
  - Rule Validation: pass/fail badge + per-rule ✓/✗ checklist
  - LLM Judge: Phase 5 stub
  - AI Visibility: Phase 10 stub (ChatGPT · Claude · Gemini)
  - Semrush panel preserved

### Status
- Phase 1–3: COMPLETE
- Optimization Workspace on Project Page: COMPLETE
- Phase 4 Rule Validation: NEXT

---

## 2026-06-29 — Session: Project Detail Page Full UI Enhancement + Spider Crawl Animation

### What Changed
- `app/templates/project_detail.html` — complete visual rewrite (layout preserved, visual layer enhanced):
  - **Status pills** under project URL: Last Crawl · Automation · Next · Firecrawl
  - **6 KPI cards**: Site Health (health bar), Pages Crawled, Total Issues, Critical Errors, Warnings, AI Suggestions — each with gradient bg, colored left border, icon, hover lift
  - **Issues by Category accordion**: color-coded left borders per category, error/warning badge counts, Fix link per issue row
  - **Crawler Settings drawer** (right-side, 500px, slide animation): 4 collapsible sections — 🕷️ Crawler, ⚡ Automation, ⚙️ Workers, 🛡️ Verification — with toggles, range slider, schedule preview, worker health chips
  - **Queue drawer** (right-side shell): tabs Running/Queued/Completed/Failed with empty state placeholders
  - **Toast system**: crawl start / audit start notifications
  - **Spider crawl animation** (fixed bar below header): CSS SVG spider with 8 animated legs (alternating gait), faces right, body bobs, moves across screen at 16s pace along a web-thread line
- `app/routes/crawl.py` — `crawl_project` and `crawl_single` now return `RedirectResponse` to `/projects/{id}` (was returning raw `pages_table.html` partial; broke layout)
- `app/routes/audit.py` — `run_audit` now returns `RedirectResponse` to `/projects/{id}` (same fix); removed unused `Request`, `Jinja2Templates`, `templates`
- `app/routes/projects.py` — computes `last_crawled_ago` string and passes to project_detail template
- `app/templates/partials/sidebar.html` — added 📦 Queue nav item with pulsing indigo badge; clicking opens the Queue drawer
- `app/templates/base.html` — added `@keyframes badgePulse` and `.q-badge` CSS globally for the sidebar badge

### Behaviour
- Crawl button triggers form POST → spider animation appears → server crawls → redirect returns full project page → spider gone
- Audit button same pattern
- ⚙️ gear icon opens Crawler Settings right-side drawer with save toast
- Queue item in sidebar opens Queue drawer (shell, no backend yet)
- All drawers close when backdrop is clicked

### Status
- Phase 1 Crawl Engine: COMPLETE
- Phase 2 SEO Audit: COMPLETE
- Phase 3 AI Suggestions: COMPLETE
- UI Enhancement (Project Detail): COMPLETE
- Phase 4 Rule Validation: NEXT

---

## 2026-06-29 — Session: Collapsible Sidebar (Hamburger Menu)

### What Changed
- `app/templates/base.html` — added hamburger `<button id="hamburger">` (three-line icon) in the header, left of the topbar slot; added CSS for `#sidebar` width + opacity transition and `.collapsed` state; added inline `<script>` that reads/writes `localStorage('sidebarCollapsed')` so collapse state persists across page loads
- `app/templates/partials/sidebar.html` — `<aside>` now has `id="sidebar"` as the CSS transition target; content wrapped in `<div id="sidebar-inner">` (fixed 210 px) so internal layout doesn't reflow when the outer element animates to width 0

### Behaviour
- Clicking the hamburger toggles the sidebar open/closed with a 220 ms ease transition
- Collapsed state is saved in `localStorage` — survives navigation and page refresh
- No JS framework required; no dependencies added

### Status
- Phase 1 Crawl Engine: COMPLETE
- Phase 2 SEO Audit: COMPLETE
- Phase 3 AI Suggestions: COMPLETE
- UI Redesign + Sidebar Hamburger: COMPLETE
- Phase 4 Rule Validation: NEXT

---

## 2026-06-29 — Session: Live AI Suggestions in Optimize Panel

### What Changed
- `app/routes/suggestions.py` — added new endpoint `POST /api/suggest?project_id=&page_id=&issue_id=`; generates 5 Claude suggestions, saves to DB, returns JSON `{"suggestions": [...]}` (no redirect); existing redirect endpoint unchanged
- `app/templates/project_detail.html`:
  - "✦ Generate AI Suggestion" button now passes `project.id`, `issue.page.id`, `issue.id` as JS args: `onclick="generateSugg('rid', projectId, pageId, issueId)"`
  - `generateSugg()` fully rewritten: `async/await fetch` → `/api/suggest` → renders 5 ranked suggestion cards with color-coded left borders; first card auto-selected and auto-populated into the Edit textarea; retry button on failure
  - `selectSugg(el)` new function: reads `data-sugg` attribute, highlights selected card, populates textarea — replaces fragile `textContent.trim()` approach
  - "Fix →" anchor removed from issue rows (only "▼ Optimize" button remains in Actions column)

### Behaviour
- User clicks "▼ Optimize" on any issue row → 7-column panel opens
- User clicks "✦ Generate AI Suggestion" → spinner shows → Claude API called → 5 cards appear (Best badge on #1, color-coded borders)
- Clicking any card highlights it and copies suggestion text into the Edit textarea
- Retry button appears on API failure

### Status
- Phase 1 Crawl Engine: COMPLETE
- Phase 2 SEO Audit: COMPLETE
- Phase 3 AI Suggestions: COMPLETE (now live in Optimize panel too)
- UI Enhancement (Project Detail): COMPLETE
- Phase 4 Rule Validation: NEXT

### Next
Add `app/validator.py` — validate each suggestion against length, keyword presence, uniqueness, readability rules; show pass/fail badges in Rule Validation column of the Optimize panel

---

## 2026-07-04 — Session: Business Profile → entity lists, PageUnderstanding, understanding-driven suggestions

### What Changed
- **Business Profile restructured from flat fields to entity lists**:
  - `app/models.py::BusinessProfile` — replaced 10 flat columns with `{brand, industry, services[], locations[], audiences[], tone, usp}` (services/locations/audiences are JSON arrays)
  - `migrations/001_business_profile_entities.py` — standalone SQLite migration converting existing flat rows losslessly (`location -> locations[0]`, `primary_market` preserved as `locations[1]`, `business_description -> usp`); verified against real data, safe to re-run
  - `app/schemas.py` (new) — `BusinessProfileIn`/`BusinessProfileOut` Pydantic models
  - `app/routes/projects.py` — `/projects/{id}/business-profile` GET/POST now take/return JSON via the new schema instead of flat `Form()` fields
  - Known follow-up: the drawer UI (`project_detail.html`) and `prompt_builder._profile_block()`/`PROFILE_FIELDS` still reference the old flat attributes and need updating separately
- **New `app/services/context_builder.py`** — one Claude call per page (temp=0, JSON-only output, retried once on malformed JSON) producing a `PageUnderstandingResult` (page_type, main_topic, search_intent, primary_keyword, secondary_keywords[], relevant_service, relevant_location, relevant_audience, geo_relevance, context_confidence) from ~3000 tokens of Fit Markdown + page metadata + the entity profile. Explicit prompt rule: `relevant_location` stays null unless the page's own content is geo-specific, not just because the profile has a location on file. Cached in new `page_understanding` table, unique on `(page_id, snapshot_id)` — a new crawl naturally invalidates the cache instead of needing an explicit expiry
- **Suggestion engine now understanding-driven**:
  - `app/routes/suggestions.py` — fetches/creates the page's `PageUnderstanding` before generating, instead of sending raw fit_markdown on every call
  - `app/prompt_builder.py` — new `build_suggestion_context()`/`resolve_profile_slice()`; the suggestion prompt now receives the understanding JSON + a narrow resolved profile slice (brand, relevant_service, relevant_location, relevant_audience, tone) instead of the full profile dump; location is included in the prompt only when `geo_relevance == "local"`
  - `app/claude.py` — `generate_suggestions()` now runs at `temperature=0.7`; `_complete()`/new `complete()` support temperature+model overrides for other callers (e.g. context_builder's temp=0)
  - `app/models.py::Suggestion` — added `understanding_id` FK -> `page_understanding.id`; `migrations/002_suggestions_understanding_fk.py` adds the nullable column to the existing table (21 existing rows preserved, backfilled NULL)
  - Output format unchanged (3-candidate numbered list) — Rule Validation is unaffected

### Behaviour
- Generating a suggestion now: loads/creates the page's cached understanding → resolves only the profile fields relevant to that specific page → builds a compact prompt (no raw page content) → Claude (temp 0.7) → 3 candidates, each `Suggestion` row tagged with the `understanding_id` used
- Regenerating for the same page reuses the cached understanding (verified: still 1 `page_understanding` row after 2 suggestion calls) — no repeat analysis cost
- Verified live against a real project: a non-geo-specific blog post correctly got `relevant_location: null` even with a location saved on the business profile

### Status
- Business Profile: restructured to entity lists (backend + migration done; UI/prompt_builder follow-up pending)
- PageUnderstanding: COMPLETE, cached, verified end-to-end with real Claude calls
- Suggestion engine: now understanding-driven, COMPLETE

### Next
Update the Business Profile drawer UI and `prompt_builder._profile_block()`/`build_meta_optimization_prompt()` to the new entity shape (currently only the suggestion-generation path was updated, per explicit scope for this session).

---
