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

## 2026-07-14 — Session: Keyword Research standalone rework (spec implementation)

Implemented `prompts/keyword-research-standalone-spec.md` end-to-end, in 6 commits (each pushed):

### Bug fixes (why the tool showed blank `—` rows)
- **Bug 1 — error propagation** (`bf16001`): `NormalizedKeyword` gained `status` ("ok"|"no_data"|"error") + `error`; adapters return explicit `{"no_data": True}` / `{"error": ...}` markers; `keyword_provider` propagates them (Semrush no_data still falls through to DataForSEO); tracking writes a `KeywordSnapshot` **only** on ok (error rolls back a just-created tracked row, HTTP 502); UI renders "No data" / "Lookup failed" badges. Fabricated zero-value snapshots can no longer poison `compute_trend()`.
- **Bug 2 — location threading** (`1164bdd`): new `app/keyword_locations.py` maps ISO country codes → Semrush database codes + DataForSEO location_codes (9 markets, IN default). Location is an explicit parameter through every fetch and route, with a market selector in the topbar. Unsupported codes are rejected (400), never silently mapped to US. `fetch_domain_metrics`' `database=us` deliberately untouched (project dashboard, not this tool).
- **Bug 3 — provider visibility** (`cbbf42e`): `GET /keywords/provider-status` + page banner (red = none configured, amber = one). Immediately diagnostic: this install has Semrush configured, DataForSEO not.

### Tests (`efe2191`)
`tests/` created — 18 pytest tests, no network (adapters mocked, snapshots as SimpleNamespace): full `compute_trend` matrix + router contract (429 → cooldown → fallback, bulk row-per-keyword, unsupported location). pytest added to requirements.

### Standalone detachment (`2071292`)
- New `KeywordWorkspace` model: keyword data hangs off workspaces, not projects; nullable `project_id` on the workspace only (spec §3.2's narrow-join-point design).
- Routes moved `/projects/{id}/keywords/*` → `/keywords/{workspace_id}/*`; `/keywords` = picker (auto-enter if only one) + create form; old project URL 303-redirects, creating the linked workspace on first use; sidebar link now `/keywords` everywhere.
- `migrations/003_keyword_workspaces.py`: backfills one workspace per project with keyword data, rebuilds tracked/saved tables with `workspace_id`. Deviation from spec's add-then-drop-later documented in the migration docstring: SQLite can't relax NOT NULL in place, so it's a single verified rebuild (row counts checked before COMMIT, mismatch rolls back). Ran live: 3 workspaces, 3 tracked + 1 saved moved, counts match, snapshot ids untouched. DB backed up first.

### UX (`1002a05`)
Bulk >100 paste shows truncation notice; View SERP renders top-10 organic results in a modal (was a raw JSON `alert`); CSV export gets UTF-8 BOM — verified end-to-end with a Hindi keyword.

### Status / open items
- All MVP checklist items pass except live provider verification with real credentials (DataForSEO creds not set in this env) and a real ≥7-day trend diff (needs calendar time).
- Rank Tracking still a stub (`position` always NULL) — Avg. Position / Easy Wins stay "Coming soon" by design.
- Provider priority order still the flagged assumption; revisit with billing data.

---

## 2026-07-18 — Keyword Research: found and fixed the real reason lookups returned nothing

### Diagnosis (live, with real credentials this time)
1. **DataForSEO account is unverified** — every real API call returns HTTP 403 / status 40104 ("Please verify your account"). Action item for VTechys: complete verification at https://app.dataforseo.com/. Until then the tool runs Semrush-only (which works: ~40k API units on the key).
2. **Semrush adapter misread every successful response.** Semrush accepts short column codes in `export_columns` (`Ph,Nq,Cp,Co,Kd`) but answers with human-readable CSV headers (`Keyword;Search Volume;CPC;...`). `_parse_csv()` kept the raw headers, so `data.get("Nq")` was always `None` → every real answer was classified `no_data` → router fell through to the dead DataForSEO account → user saw "lookup failed" even though Semrush had returned real volume. Same bug silently broke `fetch_domain_metrics` (`Or`/`Ot` keys).

### Fixes
- `semrush.py`: `_HEADER_TO_CODE` translation in both CSV parsers (+ tests pinning the real header names). Also: request Semrush's `In` intent column and map its digit codes → informational/commercial/navigational/transactional, so intent now populates from Semrush too, not only DataForSEO.
- `keyword_provider.py`: if Semrush answered `no_data` and the DataForSEO fallback then *errors*, the result is `no_data` (Semrush did answer) — not "lookup failed". Applied to single + bulk paths.
- View SERP: new `semrush.fetch_serp()` (phrase_organic, domain+URL only) as fallback; `keyword_provider.get_serp()` routes DataForSEO-first. SERP modal now works with DataForSEO down.
- `/keywords/provider-status` upgraded from "are env vars set" to a **live health check** (cached 5 min): Semrush `countapiunits` (shows units remaining), DataForSEO `dataforseo_labs/locations_and_languages` (free, sits behind the same entitlement wall that 403s unverified accounts — `appendix/user_data` alone lies, it returns 20000 even when the real API is blocked) + `user_data` for balance. Banner now prints the exact provider error, e.g. the verify-your-account message.

### Verified end-to-end (live app, port 8123)
Track `dentist in new delhi` (IN) → volume 50, KD 0 via Semrush. Track `best coffee maker` (US) → 22200 / KD 50 / commercial. Bulk: real metrics + honest `no_data` for a nonsense phrase. Suggestions (Semrush fallback) return real volumes+intent. SERP modal shows top-10 via Semrush fallback. CSV export with BOM intact. 29/29 tests pass (11 new).

### Open items
- **DataForSEO verification** — the one thing code can't fix; once verified, intent-rich suggestions, full SERP descriptions, and the second provider light up with zero code change (banner will go away by itself).
- Trend arrows still need ≥7 days of snapshot history to leave "Pending".

---

## 2026-07-18 (later) — Keyword Research: "Worth It" scoring, Claude briefs, and the 16-point UX upgrade

Implemented the user's 16-item enhancement list end-to-end:

### New capabilities
- **Worth It score (USP)**: `app/keyword_scoring.py` — 0-10 verdict per keyword from volume (log scale, 0-4 pts) + difficulty (0-4) + intent value (0-2) + SERP-feature penalty (AI Overview −1.2, ads up to −0.8, snippet, map pack). Bands: 🟢 easy ≥7.5 / 🟡 medium / 🔴 avoid <4. Factors list = plain-language explanation, shown in the expand row + tooltip. Weights are deliberately all in one file for future tuning.
- **Claude content briefs**: `POST /keywords/{ws}/brief` — metrics + live SERP + question keywords go to Haiku via `prompt_builder.build_keyword_brief_prompt()`; returns a client-ready Markdown brief (intent, angle to win, title, outline, FAQs, AI-visibility tips). ~1¢/brief. Rendered in a modal with copy button.
- **Expand row / detail**: `GET /keywords/{ws}/detail` — one call returns metrics + SERP-aware Worth It + top-10 results + SERP feature chips (🤖 AI, 📦 Ads×N, 📍 Maps, …) + questions. Cached client-side per keyword+location. (Avg DR / word count / backlinks deferred — needs per-URL backlink API spend.)
- **Sparklines**: swapped Semrush `phrase_all` → `phrase_this` (same call, same units) which honors the `Td` 12-month trend column that phrase_all silently drops. Stored on snapshots via additive migration 004 (`trend_points` TEXT) so Overview reloads keep them.
- **Suggestion modes**: seed + checkboxes (Related/Questions/Prepositions/Comparisons) — related/questions DataForSEO-first as before; prepositions/comparisons filter Semrush `phrase_fullsearch` broad matches by word lists. Grouped sections in one table, dedup across groups.
- **UI rework** (client-rendered tables): filters (intent/min-vol/max-KD/include/exclude/easy-only), intent+volume distribution mini bar charts, checkbox selection with sticky action bar (save/track/export-selected-CSV/untrack), hover-revealed icon actions (👁 ⚡ 📋 📈 ♡/❤️ ✕), standardized intent colors (txn green / info blue / commercial orange / nav purple / local teal), compact dismissible provider banner with "Verify Account →" link when DataForSEO reports the verification error.
- **Stat cards**: Tracked / Total Volume / Easy Wins (= Worth It band, honest and available today, replacing position-based placeholder) / Avg KD.
- Also: Semrush multi-intent values ("1,0") now take the dominant code.

### Verified live
Track `dentist near me` (IN) → 90.5K, KD 56, transactional, 12 trend points, Worth It 7.1 🟡 with 4 factors. Grouped suggestions: 20 questions / 39 prepositions / 1 comparison for "dentist". Detail: 10 SERP results (Semrush fallback), questions, SERP-aware score. Claude brief generated and correctly identifies the aggregator-gap angle. 39/39 tests pass (10 new).

### Notes
- SERP feature chips show "Not available" until the DataForSEO account is verified (Semrush fallback carries no features); scoring then says "SERP features not checked yet" instead of pretending.
- Old `/keywords/{ws}/{id}/serp` endpoint kept (API compat); UI now uses /detail.

---

## 2026-07-18 (later still) — Keyword Research: worker-facing user guide

Wrote `docs/keyword-research-user-guide.md` — a plain-language guide for
VTechys marketing workers with no SEO/technical background, covering every
control on the page: top bar (market selector, export, add keywords, status
banner), the 4 stat cards, all 5 tabs, how to read a keyword row, the Worth
It score in depth (what it means, how to read the factor breakdown, when it
honestly says "not checked yet"), the expand row / SERP feature icons, the
⚡ Generate Content Brief flow, hover row actions, filters, the two mini
charts, bulk selection actions, and a "quick reference — what to do when"
table at the end.

No new code — documentation only, written against the UI shipped in
commits `ee670e3` and `3b48c88` today. Intended to be the thing a worker
opens instead of asking someone how a button works.
