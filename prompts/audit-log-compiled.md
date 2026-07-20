# VTechys SEO Platform — Compiled Audit Log

Consolidated record of every codebase audit run during this development
cycle. Each audit was executed by Claude Code against the live repo/DB,
not inferred — evidence is file:line or live query results as captured at
the time. Use this as a dated record of platform state for development
tracking / stakeholder evidence.

---

## Audit 1 — Roadmap Status (V1–V11 Versioning Roadmap)

**Scope:** Full codebase check against the original V1–V11 roadmap.

| Version | Feature | Status | Evidence |
|---|---|---|---|
| V1 | Crawl Engine | ✅ Done | `crawler.py::_extract_page_data` pulls title, meta desc, meta keywords, h1/h2, heading structure, image alts, domain/page JSON-LD schema, canonical, OG, Twitter, lang. `crawl_single_page()` and `crawl_site()` (sitemap+BFS) both exist. Snapshots in `CrawlSnapshot`. |
| V1.5 | Audit Engine | ✅ Done | `audit.py` RULES covers title/meta/H1/H2/alt/schema/canonical/OG/Twitter/lang/thin-content. `page_score()` 0–100. |
| V2 | Snapshot System | 🟡 Partial | Snapshots stored (`CrawlSnapshot`) but never diffed — no fixed/new/regressed-issue logic, no score-delta output found anywhere in `app/`. |
| V3 | AI Suggestions | 🟡 Partial (at time of audit) | Claude integration works, stores `Suggestion` rows. Generates 3 not 5 (`SUGGESTION_COUNT = 3`). Regeneration deleted all prior suggestions for the issue (no history) — **superseded by later findings, see Audit 6.** |
| V4 | Rule Validation | ✅ Done (mostly) | `audit.py::validate_value()` re-runs real rules for title/meta/h1/h2/canonical/lang. Keyword-presence/uniqueness/readability not implemented. |
| V5 | LLM Judge | ❌ Not started | Only a dead stub `push_judge()`, never called. |
| V6 | Acceptance Tracking | ❌ Not started (at time of audit) | No status/accepted/rejected fields on `Suggestion`. **Superseded — see Audit 6, now built.** |
| V7 | Learning Dataset | 🟡 Stub only | Supabase push functions exist for all 4 tables, zero callers anywhere. |
| V8 | RivalFlow | ❌ Not started | Zero matches for competitor/content-gap in repo. |
| V9 | RAG | ❌ Not started | No embeddings/vector/similarity logic. |
| V10 | AI Visibility Prediction | ❌ Not started | Only the unused `push_visibility` stub. |
| V11 | WordPress Deploy | ❌ Not started (at time of audit) | No deploy/rollback/plugin code found. **Superseded — see Audit 6, now built.** |

**Plus, outside the roadmap:** Keyword Research found fully built — workspaces, tracked/saved keywords, dual-provider fetch, snapshot-based trend/position diffing — more mature than several roadmap items above it (though later found to have live bugs — see Audit 3).

---

## Audit 2 — Sprint 3 Track A: Crawler Settings & Scheduling

| Component | Status | Evidence |
|---|---|---|
| Settings form UI | ✅ Done (visual only, at time of audit) | `project_detail.html:705-796` — full form: User Agent, Max Depth, Crawl Delay, Timeout, robots.txt toggle, Exclude patterns, Automatic Crawling toggle, Schedule dropdown, Timezone, Custom Cron, Coverage Target, Worker settings. |
| Save → JS call | ❌ Not started (at time of audit) | `project_detail.html:972-975` — `saveSettings()` only called `closeDrawer()` + `showToast('✅ Settings saved')`. No fetch, no persistence. **Superseded — see Audit 5, now real.** |
| Backend route | ❌ Not started (at time of audit) | Zero matches for "settings" in `app/routes/*.py`. **Superseded — see Audit 5.** |
| DB persistence | ❌ Not started (at time of audit) | `Project` model had no schedule/settings columns. **Superseded — see Audit 5 (`Schedule`/`Job` tables now exist).** |
| Discovery layer | ✅ Done | `crawler.py:269-282` parses `robots.txt` `Sitemap:` directives + common paths; `243-266` recurses nested `<sitemapindex>`; `304-329` same-domain link BFS fallback; wired into the live `crawl_site()` path used by `POST /projects/{id}/crawl`. |
| Scheduler mechanism | ❌ Not started (at time of audit) | Zero matches for APScheduler/Celery/cron/BackgroundTasks/loop anywhere in `app/`. **Superseded — see Audit 5.** |
| CrawlJob/schedule model | ❌ Not started (at time of audit) | Only `CrawlSnapshot` existed (results, not a queue). **Superseded — see Audit 5.** |
| Queue badge | ❌ Not started (at time of audit) | Hardcoded "Not scheduled" text, no backing query. |
| Queue drawer job list | ❌ Not started (at time of audit) | Hardcoded "No {tab} jobs" placeholder in all 4 tabs, no loop over real data. |
| Crawl execution (context) | ✅ Done (synchronous only) | `routes/crawl.py:48-65` — crawl runs synchronously inside the request handler, only on user click. |

**Summary at time of audit:** UI was "a convincing fake" for everything except the crawl itself and discovery. Settings persistence, job/schedule table, and scheduler were flagged as the real remaining work. **All since built — confirmed in Audit 5.**

---

## Audit 3 — WordPress Deploy Path: Post-ID Resolution, Connection State, Duplicate Suggestions

| # | Claim | File:Line | Evidence |
|---|---|---|---|
| 1 | Prompt message is JS `prompt()`, seeded by server-generated detail string | `project_detail.html:1667-1678`, `wordpress.py:191-194` | JS: `prompt((d.detail || "Couldn't auto-detect...") + '\n\nEnter it manually (numeric):')`. Server: `HTTPException(400, detail="Could not determine the WordPress post ID...")`. |
| 2 | Deploy chain fully traced, no gaps | `project_detail.html:1578` → `wordpress.py:224-249` → `wordpress.py:176-194` → `wordpress.py:71-99` | Button → `POST /suggestions/{id}/deploy` → `_resolve_wp_post_id()` → `deployer["write"]` → `_call_tool()` → plugin's `/wp-json/cwpm/v1/tool`. |
| 3 | Three real fallback layers for `wp_post_id` | `wordpress.py:176-194` | explicit request param → cached `Page.wp_post_id` → one live resolve attempt → 400 error. Not hardcoded, not absent. |
| 4 | `Page.wp_post_id`/`wp_post_type` columns exist | `models.py:97-103` | Confirmed present. |
| 5 | URL→post-ID resolution attempted automatically at crawl time | `routes/crawl.py:19-39`, called at `crawl.py:62` | `_maybe_resolve_wp_post_id()` runs inside `upsert_page()` on every crawl/re-audit. Manual entry is last resort, not the only path. |
| 6 | Manually-typed ID is NOT persisted (bug) | `wordpress.py:184-190` | Live-resolve fallback writes `page.wp_post_id` + commits. But a typed manual value returned directly at the `if explicit is not None` branch is never written back — asked again next time. |
| 7 | `app/wordpress.py` matches scaffolded design | full file | `WordPressResult` (L57), `_call_tool` (L71), `set_yoast_meta` (L162), `test_connection` (L144) all present. |
| 8 | `WordPressConnection` row exists for project 1, verified | live DB query | `{'id':1, 'project_id':1, 'site_url':'https://vseo.vtraffic.io', 'is_staging':1, 'last_verified_at':'2026-07-19 11:50:00', 'last_verify_ok':1}` |
| 9 | All 25 pages in project 1 have `wp_post_id = NULL` | live DB query | Every row returns `None, None`. |
| 10 | Live resolution works right now against `vseo.vtraffic.io` | live call | `resolve_post_id_by_url(..., '/blog')` → `post_id=2015`. `resolve_post_id_by_url(..., '/a-complete-ppc-checklist...')` → `post_id=11024`. |
| 11 | "Not linked to a WordPress post yet" renders purely from `d.wp_post_id` falsy | `project_detail.html:1606-1611` | Ternary sourced from `projects.py:481`. |
| 12 | Plugin has no `/tools` route (404), has `/capabilities` instead | live call | `GET /wp-json/cwpm/v1/tools` → 404. `GET .../capabilities` → 200, full tool list. |
| 13 | No dedicated slug/URL lookup tool; `list_posts` closest fit | capabilities response | `list_posts` filterable by type/status/author/date/meta — slug/url param unconfirmed at audit time. |
| 14 | `get_options` tool exists, relevant to homepage resolution | capabilities response | Could read `show_on_front`/`page_on_front` to resolve the static-front-page case. |
| 15 | Two real duplicate Suggestion rows, not a render bug | live DB query | `id=61` (12:52:48) and `id=62` (13:14:21), identical content, both `status=accepted`, both `issue_id=133`. |
| 16 | Mechanism: delete-before-insert protects decided rows, second generation produced identical text | `suggestions.py:40-45` + timestamps | `.filter(status.notin_(DECIDED_STATUSES)).delete()` — `accepted` survives deletion; second batch (62/63/64) inserted fresh alongside it. |
| 17 | No uniqueness constraint on `(issue_id, content)` | live schema dump | Confirmed — only implicit `id` PK. |
| 18 | Render loop is a clean 1:1 map, not a duplication bug | `project_detail.html:1590` | Standard `.map()` over `issue.suggestions`, sourced from real SQLAlchemy relationship — two DB rows in, two cards out. |

**Verdict at time of audit:** (a) nothing architecturally missing for post-ID resolution — trigger gap only (pages crawled before connection existed). (b) WordPress connection fully configured and verified. (c) genuine data problem — two real duplicate rows, not a render bug. **All items addressed in the consolidated WordPress deploy fix task — see development log for implementation.**

---

## Audit 4 — Backlink Analysis: Current Build State

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | `BacklinkSnapshot` / `BacklinkRecord` models | ✅ Done | `models.py:297` `BacklinkSnapshot` (authority_score, referring_domains, total_backlinks, follow/nofollow, source, fetched_at). `models.py:315` `BacklinkRecord` (source_url, target_url, anchor_text, is_follow, first/last_seen_at, lost_at). |
| 2 | Provider + `backlinks_overview` wiring | ✅ Done | `app/backlinks_provider.py` wraps `semrush.py:116 fetch_backlinks_overview` + `:158 fetch_backlinks_list`, called from `routes/projects.py:315` and `jobs/handlers/backlink_pull.py:35,48`. |
| 3 | Route/nav | 🟡 Partial | Real routes exist (`GET/POST /projects/{id}/backlinks`, `/refresh`, `/records`, schedule endpoint) but `sidebar.html:70` renders a disabled placeholder link (`href="#"`, `enabled=False`) — feature unreachable via nav. |
| 4 | New/lost diffing, anchor analysis, toxic heuristic | ✅ Done (diffing/anchor); ❌ toxic not started | `backlink_pull.py` job does new/lost diffing, registered as `backlink_pull`. `anchor_text`/`is_follow` used throughout. Zero hits for "toxic" anywhere — not built. No test file for backlinks. Migration `010_backlinks.py` applied, tables live. |
| 5 | Domain-metrics panel overlap | Separate, not overlapping | Pre-existing domain-metrics numbers use `fetch_domain_metrics()`, unrelated to this feature — no accidental double-build. |

**Verdict:** Far more built than the greyed-out nav implied — backend and frontend fully wired end-to-end, live in DB. Real gaps: dead sidebar link (one-line fix), no toxic-score field, no test coverage.

---

## Audit 5 — Rank Tracking: Current Build State (includes Job/Schedule system confirmation)

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | `rank_check` handler + registry | ✅ Done | `jobs/handlers/rank_check.py::run_rank_check_job` — pulls `TrackedKeyword`s, calls `get_serp()`, finds project domain in results, writes `KeywordSnapshot(position=...)`. Registered identically to `backlink_pull` in `JOB_HANDLERS` dict. |
| 2 | Rank Tracking Save toggle | ✅ Done — real backend | `keyword_research.html:166 saveKeywordSchedule('rank_check')` performs genuine `fetch()` POST to `/keywords/{workspace_id}/schedule/rank_check` → `routes/keywords.py:254-294` upserts a real `Schedule` row and commits. |
| 3 | Generic Job/Schedule system | ✅ Done — shared across all job types | `Job` (`models.py:334`) and `Schedule` (`models.py:355`, unique on `project_id`+`job_type`) are generic tables. `crawl`, `rank_check`, `keyword_refresh`, `backlink_pull` all share one `schedules` table — confirmed via identical upsert pattern in `routes/projects.py` and `routes/keywords.py`. No bespoke per-feature scheduler. |
| 4 | `KeywordSnapshot.position` writes | ✅ Done — actually populated | Only write site in the repo: `rank_check.py:83-91`. Elsewhere `position` is read-only. |
| 5 | Easy Wins card gating | ✅ Done — honest flag | `keyword_research.html:186-194` branches on `overview.data_quality == "live"`, computed server-side (`routes/keywords.py:123-128`) — flips to `"live"` only once real position data exists, not a dummy flag. |

**Verdict at time of audit:** Not decorative — real backend end-to-end. **One unverified thread flagged:** the scheduler's automatic tick loop (vs. manual force-run) had not been independently observed. **Resolved in Audit 7 below.**

---

## Audit 6 — Suggestion UI Duplication: Category-Row View vs. "Fix on Page" Modal

| # | Claim | File:Line | Evidence |
|---|---|---|---|
| 1a | Category-row "Save" does nothing | `project_detail.html:570` | `onclick="console.log('Save:', ...)"` — console only, no fetch, no persistence. |
| 1b | Category-row "Generate AI Suggestion" does persist | `project_detail.html:1161-1166` → `suggestions.py:68-77` | `POST /api/suggest` → `_generate_and_store()`, writes real `Suggestion` rows. |
| 1c | Selecting a card has no DB link | `project_detail.html:1208-1226` | `selectSugg()` sets textarea value from plain text `data-sugg` — no suggestion id ever attached to the DOM. |
| 2a | "Fix on Page" opens modal via a fresh, independent fetch | `project_detail.html:649-654`, `projects.py:409-461` | `openPageModal(pageId)` → `GET /projects/{id}/pages/{id}/detail-json`, keyed only on `page_id` — no issue/suggestion context passed. |
| 2b | Modal shows current title, not the selection | `project_detail.html:1578` | Renders `d.title` — the page's live crawled title, no awareness of the category-row textarea. |
| 3 | Same table, same generation call, two route paths | `suggestions.py:62-77` | Both `POST /api/suggest` and `POST /projects/{id}/pages/{id}/issues/{id}/suggest` call identical `_generate_and_store()`. Routing duplication, not data duplication. |
| 4 | Full accept/reject/status state machine already exists | `models.py:147-150` | `status` (pending/accepted/rejected/edited/deployed), `edited_content`, `accepted_at`, `deployed_at` — all present, all wired to real endpoints. |
| 5 | "Fix on Page" is a pure display/navigation action | `project_detail.html:649` vs `projects.py:433-461` | Button `onclick` only calls `openPageModal()` → GET. No write until user acts inside the modal separately. |

**Verdict:** (b) — one data system, two inconsistent, disconnected UI views of it. The category-row accordion is an older, unfinished front; the modal is the only view wired to the full accept/reject/edit/deploy/rollback state machine. **Consolidation task written and shipped following this audit.**

---

## Audit 7 — Scheduler Tick Loop, End-to-End Verification (live, not code-read only)

**Method:** live app run, not static code inspection alone.

- `app/scheduler.py` confirmed: `dispatch_due_schedules()` runs every 60s, queries `Schedule` rows where `enabled AND next_run_at <= now`, creates a `Job` per due schedule, advances `next_run_at`. `run_next_queued_job()` (at time of this audit — since split, see below) ran every 10s, picked the oldest queued `Job`, ran it via subprocess, `max_instances=1`. Wired in `app/main.py:17` (`job_scheduler.start()` in lifespan) — not dead code.
- **Live automatic firing confirmed:** `dispatch_due_schedules` created 3 real `Job` rows (crawl, rank_check, crawl) and `run_next_queued_job` picked one up and transitioned it `queued → running`, without any manual trigger — this was the one previously-unverified link in the whole automation chain.
- **Real production finding surfaced by this test, not by design review:** a single long-running `crawl` job (6+ minutes against `vseo.vtraffic.io`) occupied the single-worker queue, starving an already-due `rank_check` job behind it. Root cause: `max_instances=1` shared across all job types, oldest-queued-first with no per-type isolation.

**Verdict:** scheduler mechanism itself confirmed real and working automatically. Starvation risk confirmed live, not hypothetical — **fixed same day, see Fix Log below.**

---

## Audit 8 — WordPress Plugin Capabilities: Security Introspection Readiness

**Method:** direct read of plugin PHP source (`scripts/claude-wp-mcp final/claude-wp-mcp/`), not an API response guess.

| Signal | Achievable now? | Tool | Notes |
|---|---|---|---|
| 1. List users (roles, email, registration) | ✅ Yes | `list_users` | `handler-wordpress.php:36-60` — returns id, masked email, name, roles[], registered date, filterable/paginated. `get_user` for single-user detail. |
| 2. Read `wp_options` cron | ✅ Yes | `get_options(keys=['cron'])` or `run_cron` | `cron` not in `BLOCKED_OPTIONS` (`handler-theme.php:8-18`, which blocks `siteurl`, `active_plugins`, auth keys/salts). `run_cron` gives a cleaner purpose-built read of next 20 scheduled events. |
| 3. List/read plugin or theme files | ❌ No | — none | No file/read/list_files tool anywhere in the 65-tool dispatcher. `php_exec` exists but is confirmation-gated and dev/staging-flagged — not a scoped file tool. |
| 4. List installed plugins (slug + version) | ✅ Yes | `list_plugins` | `handler-plugins.php:5-25` — iterates WP's own `get_plugins()`, returns all plugins including deactivated ones. Does not catch file-system-level hiding techniques (same gap as #3). |

**Additional finding:** `/wp-json/cwpm/v1/capabilities` (`class-router.php:206-290`, `tool_manifest()`) under-reports the true callable surface — 6 tools (`elementor_list_templates`, `elementor_import_template`, `list_forms`, `list_form_entries`, `get_form_entry`, `list_email_logs`) are live and dispatchable via `POST /wp-json/cwpm/v1/tool` but have no manifest entry, so self-enumeration via `/capabilities` is not a fully trustworthy source of truth for what's actually callable.

**Verdict:** 3 of 4 security-introspection signals (rogue admins, cron injection, hidden/deactivated plugins) are achievable today with zero plugin changes. File-level backdoor detection requires either a new scoped `list_files`/`read_file` tool from the plugin author, or a separate file-system/SFTP access path.

---

## Fix Log (post-audit, verified live — included for continuity)

- **Job queue starvation (from Audit 7):** implemented separate worker lanes —
  `run_next_crawl_job` (crawl only, 900s timeout) and `run_next_light_job`
  (rank_check/keyword_refresh/backlink_pull/audit, new 180s timeout), each
  ticking every 10s with independent `max_instances=1`. Verified live: crawl
  and rank_check fired together, rank_check completed in 9s while crawl was
  still running minutes later — the exact starvation scenario reproduced and
  confirmed fixed. 71/71 tests pass.
- **Follow-up opened, not yet root-caused:** `KeywordSnapshot.position` landing
  `NULL` on every `rank_check` run in live testing — logged as a separate
  audit-then-fix unit, not bundled into the queue fix. Status: audit pending.

---

*Compiled from Claude Code audit outputs across this development cycle.
Each entry reflects the codebase state at the time that specific audit was
run — later audits or the Fix Log supersede earlier findings where noted.*
