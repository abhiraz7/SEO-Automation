# VTechys — Master Task List (Claude Code Execution Plan)

Every task below is sized to be completed by Claude Code in one focused
session (30–90 min each). Each has: files to touch, exact scope, and a
DONE-check you can verify without being an SEO expert — just follow the
"How you verify" step and see if it happens.

Rules for running these:
- Do them IN ORDER within a phase. Phases 1 and 2 can run in parallel.
- Paste one task at a time into Claude Code as the prompt.
- After each task, run the verify step yourself before moving on.
- If a task fails its verify step, tell Claude Code exactly what you saw —
  don't move forward on a broken foundation.

---

## PHASE 1 — Fix Keyword Research (it's built but lying to users)

### Task 1.1 — Honest provider errors
**Prompt for Claude Code:**
"In app/keyword_provider.py and app/dataforseo.py and app/semrush.py:
provider failures are currently normalized into blank-but-valid
NormalizedKeyword objects, so API errors, missing credentials, and genuine
no-data all render identically as empty rows. Refactor so every provider
call returns an explicit three-outcome result: ok (with data) / no_data /
error (with a human-readable reason). Propagate this through
routes/keywords.py so the API responses include the outcome, and update
keyword_research.html so each state renders differently: real metrics,
'No data for this keyword', or a visible error message. A failed lookup
must NOT write a KeywordSnapshot row."
**Files:** keyword_provider.py, dataforseo.py, semrush.py, routes/keywords.py, keyword_research.html
**How you verify:** temporarily rename SEMRUSH_API_KEY in .env, restart,
track a keyword → you must see an error message, not a blank row. Restore
the key → real numbers appear.

### Task 1.2 — Location support (India first)
**Prompt for Claude Code:**
"app/semrush.py hardcodes database=us and app/dataforseo.py hardcodes
location_code 2840 (US). Add a location parameter threaded from the routes
through keyword_provider into both adapters. Semrush uses country database
codes (in, us, uk...); DataForSEO uses numeric location codes (India=2356,
US=2840). Add a small mapping table for at least: IN, US, UK, AU, CA, AE.
Add a location selector dropdown to keyword_research.html defaulting to
India. Persist the chosen location per tracked keyword so refreshes use
the right market."
**How you verify:** track 'dentist in new delhi' with location India →
real volume appears (Semrush is confirmed working). Switch to US → likely
'No data', which is correct and honest.

### Task 1.3 — Provider status visibility
**Prompt for Claude Code:**
"Add GET /keywords/provider-status returning, for each provider (semrush,
dataforseo): configured (credentials present) and last known working state.
Show a small banner on keyword_research.html when a provider is
unconfigured or failing, e.g. 'DataForSEO: account not verified'. Do not
call providers on every page load — cache the last probe result."
**How you verify:** with DataForSEO still unverified, the page shows a
banner saying so instead of silently missing data.

### Task 1.4 — Semrush primary for keywords
**Prompt for Claude Code:**
"In keyword_provider.py, make Semrush the primary provider for keyword
overview, bulk, related, and questions (it is confirmed entitled and
working on this account), with DataForSEO as fallback. Keep the existing
rate-limit cooldown logic. Leave a comment that this priority is based on
verified account entitlements as of July 2026."
**How you verify:** track a keyword → data appears even though DataForSEO
is still blocked at verification.

---

## PHASE 2 — Generic Job System (the engine every tool will run on)

### Task 2.1 — Job + Schedule tables
**Prompt for Claude Code:**
"Add two models to app/models.py: Job (id, project_id FK, job_type str,
status str default 'queued' [queued/running/completed/failed/cancelled],
payload JSON, result_summary JSON, error Text, attempts int default 0,
scheduled_for, started_at, finished_at, created_at) and Schedule (id,
project_id FK, job_type, enabled bool default true, interval str
[24h/12h/6h/weekly/cron], cron_expression nullable, timezone default
'Asia/Kolkata', payload JSON, last_run_at, next_run_at; unique constraint
on project_id+job_type). Write an additive migration following the same
pattern as the existing business-profile migration. Do not modify any
existing tables."
**How you verify:** app starts clean; new tables visible in the DB.

### Task 2.2 — Make Crawler Settings Save real
**Prompt for Claude Code:**
"project_detail.html's saveSettings() (~line 972) only closes the drawer
and shows a fake success toast — no request is sent and no endpoint
exists. Create POST /projects/{id}/crawl-settings that upserts a Schedule
row with job_type='crawl': automation fields (enabled toggle, schedule
dropdown, timezone, cron) map to Schedule columns; crawler behavior fields
(user agent, max depth, delay, timeout, robots.txt, exclude patterns,
worker settings) go into Schedule.payload. Update saveSettings() to
fetch() this endpoint with the form values and only show the success toast
on HTTP 200; show an error toast otherwise. On page load, populate the
drawer's fields from the saved Schedule row if one exists."
**How you verify:** open Crawler Settings, set schedule to Weekly, save,
refresh the page, reopen the drawer → Weekly is still selected.

### Task 2.3 — Job registry + crawl handler
**Prompt for Claude Code:**
"Create app/jobs/__init__.py and app/jobs/registry.py with a
JOB_HANDLERS dict mapping job_type strings to handler functions. Create
app/jobs/handlers/crawl.py with run_crawl_job(db, job) that: marks the
job running with started_at, calls the existing crawler.crawl_site() for
the project's base_url using settings from the job payload, stores a
result summary (pages crawled count), marks completed/failed with
finished_at and error. Handlers must never raise — always record failure
on the job row."
**How you verify:** nothing user-visible yet; ask Claude Code to also add
a temporary POST /projects/{id}/jobs/test-crawl route that creates and
immediately runs a crawl Job, and confirm a Job row ends as 'completed'.

### Task 2.4 — APScheduler runner
**Prompt for Claude Code:**
"Add APScheduler (BackgroundScheduler) to the FastAPI app lifecycle
(startup/shutdown events in app/main.py). Two responsibilities: (1) every
60s, find enabled Schedule rows where next_run_at <= now, create a Job row
from each, and advance next_run_at per the interval/cron; (2) a worker
tick that picks the oldest queued Job, looks up its handler in
JOB_HANDLERS, and runs it — one job at a time (single worker) to keep
SQLite happy. Also ensure the SQLite engine sets PRAGMA journal_mode=WAL
and busy_timeout=5000 on connect (add the SQLAlchemy connect event if not
present). On startup, compute next_run_at for any Schedule rows where it
is null."
**How you verify:** set a project's crawl schedule to enabled with a
next_run_at forced to now (Claude Code can add a small dev endpoint),
wait ~2 minutes without clicking anything → a new CrawlSnapshot appears.
The site crawled itself. This is the moment the platform becomes
automated.

### Task 2.5 — Feed the Queue drawer real data
**Prompt for Claude Code:**
"The Queue drawer in project_detail.html (~lines 893-928) shows hardcoded
'No {tab} jobs' placeholders, and the sidebar 📦 badge is static. Add GET
/projects/{id}/jobs?status=... returning Job rows (type, status, timestamps,
result summary/error). Replace the placeholder blocks with rendered job
rows per tab (Running/Queued/Completed/Failed map directly to Job.status;
show completed+failed limited to last 20). Make the badge show the live
count of queued+running jobs for the current project, polling every 10s
while the page is open. Keep the existing drawer design — populate it,
don't redesign it."
**How you verify:** trigger a crawl → open the Queue drawer → the job
appears under Running, then moves to Completed. Badge counts change.

---

## PHASE 3 — Acceptance + WordPress Deploy (the Semrush-can't-do-this part)

### Task 3.1 — Suggestion acceptance tracking
**Prompt for Claude Code:**
"Add to models.Suggestion: status (default 'pending';
pending/accepted/rejected/edited/deployed), accepted_at, deployed_at,
edited_content Text nullable. Additive migration. Add POST endpoints in
routes/suggestions.py: accept, reject, edit (stores edited_content and
sets status='edited'). Add Accept / Reject / Edit buttons to the
suggestion cards in the UI, reflecting current status with a colored
badge. Regeneration must no longer delete suggestions that have status
accepted/edited/deployed — only pending/rejected ones may be replaced."
**How you verify:** accept a suggestion, refresh → it still shows
Accepted. Regenerate → the accepted one survives.

### Task 3.2 — WordPress connection storage
**Prompt for Claude Code:**
"Add WordPressConnection model (project_id unique FK, site_url, api_token,
is_staging bool default true, last_verified_at, last_verify_ok). Encrypt
api_token at rest using cryptography.fernet with a key from env var
WP_TOKEN_KEY (generate-if-missing helper + README note). Add app/wordpress.py
adapter [use the scaffold provided in the repo/outputs if present]: calls
POST {site_url}/wp-json/cwpm/v1/tool with Bearer auth, returns explicit
ok/no_data/error results, functions: set_yoast_meta, update_post_content,
update_media_alt_text, test_connection. Add a 'WordPress' section to the
project settings UI: site URL + token fields + Test Connection button
hitting a new POST /projects/{id}/wordpress/test endpoint."
**How you verify:** enter vseo.vtraffic.io + the plugin's token, click
Test Connection → green success. Wrong token → clear error.

### Task 3.3 — Deploy one field end-to-end (THE proof point)
**Prompt for Claude Code:**
"Add SuggestionRevision model (suggestion_id FK, project_id FK, field_name,
before_value, after_value, wp_post_id, deployed_via, deployed_at,
rolled_back_at nullable, deploy_result_raw JSON). Add POST
/suggestions/{id}/deploy: only allowed for accepted/edited suggestions on
projects with a verified WordPressConnection; fetches current value first
(for before_value), writes a SuggestionRevision row, calls
wordpress.set_yoast_meta for meta-description suggestions, sets
suggestion status='deployed' on success, surfaces the adapter error on
failure without writing a revision. Add a Deploy button that appears only
on accepted suggestions. Meta description only in this task — no other
field types yet."
**How you verify:** accept a meta-description suggestion → Deploy → open
the staging WordPress page source → the new meta description is live.
When this works, the core product promise is real.

### Task 3.4 — Rollback
**Prompt for Claude Code:**
"Add POST /revisions/{id}/rollback: calls wordpress.set_yoast_meta with
the revision's before_value, sets rolled_back_at, sets the suggestion
back to status='accepted'. Add a Revision History panel on the project
page listing SuggestionRevision rows (field, before→after, when, deployed/
rolled back) with a Rollback button on active revisions."
**How you verify:** deploy a fix, roll it back, check the staging page →
old value restored.

### Task 3.5 — Expand deployable fields
**Prompt for Claude Code:**
"Extend the deploy path to: meta title (yoast_set_meta), H1/post content
(update_post_content), image alt text (update_media_alt_text). Each
deploy writes its own SuggestionRevision. Keep per-field routing in one
place (a FIELD_DEPLOYERS mapping) so future field types are one entry."
**How you verify:** deploy a title fix and an alt-text fix; both appear
on staging; both show in Revision History; both roll back cleanly.

---

## PHASE 4 — Rank Tracking + automated data collection (cheap now)

### Task 4.1 — Rank check job
**Prompt for Claude Code:**
"Add app/jobs/handlers/rank_check.py: for each TrackedKeyword in the
project, query the SERP via the existing provider layer (Semrush primary)
for the keyword+location, find the project's domain position in the top
100, write it to a new KeywordSnapshot row's position column (schema
already has it). Register as job_type='rank_check'. Add a schedule widget
(same interval dropdown pattern as crawler) to the Keyword Research page
creating/updating a Schedule row with job_type='rank_check'."
**How you verify:** enable daily rank tracking, force next_run_at to now →
after the job completes, tracked keywords show a real Position instead of
'—', and the Avg. Position stat card comes alive.

### Task 4.2 — Keyword refresh job
**Prompt for Claude Code:**
"Add jobs/handlers/keyword_refresh.py: re-fetch volume/difficulty for all
TrackedKeywords, writing new KeywordSnapshot rows (respecting the honest
error handling — failed fetches write nothing). Register as
job_type='keyword_refresh', default weekly. This makes compute_trend()'s
≥7-day comparison actually possible over time."
**How you verify:** after two runs ≥7 days apart (or seed test data),
Trend column shows Rising/Stable/Falling instead of eternal 'Pending'.

### Task 4.3 — Easy Wins card
**Prompt for Claude Code:**
"The 'Easy Wins' stat card says Coming Soon. Now that position data
exists, implement it: keywords ranking position 4-20 with difficulty
below 50 (tune thresholds in one constant). Clicking it filters the
Overview table to those keywords."
**How you verify:** with rank data present, the card shows a count and
clicking filters the table.

---

## PHASE 5 — Backlinks + auto-audit (per the existing backlinks spec)

### Task 5.1 — Backlink models + Semrush pull
(Follow backlinks-tool-spec.md §3-§4: BacklinkSnapshot, BacklinkRecord,
backlinks_provider.py with the three-outcome contract, Semrush
backlinks_overview wired to an Overview tab.)
**Verify:** project shows real authority score / referring domains.

### Task 5.2 — Backlink diffing job
(job_type='backlink_pull', weekly; new/lost detection per spec §3.)
**Verify:** after two pulls, New/Lost tabs show real changes.

### Task 5.3 — Auto-audit after crawl
**Prompt for Claude Code:**
"At the end of run_crawl_job, create an 'audit' Job for the same project.
Add jobs/handlers/audit.py wrapping the existing audit engine. Scheduled
crawls now produce fresh audits with zero clicks."
**Verify:** a scheduled crawl completes → issue counts update on their own.

---

## PHASE 6 — Hardening + production (from the deployment pipeline doc)

- 6.1 Postgres migration (before job volume grows: schema move, data copy,
  row-count verification)
- 6.2 /health endpoint + deploy.ps1 + NSSM + Caddy per the nine-phase VPS
  plan already written
- 6.3 Basic security audit rules in audit.py: SSL, security headers,
  robots.txt presence (cheap adds to the existing RULES list)
- 6.4 Full end-to-end QA of every phase's verify steps against production

---

## Progress tracker (update as you go)

| Phase | Tasks | Done |
|---|---|---|
| 1 Keyword fixes | 1.1–1.4 | ☐ ☐ ☐ ☐ |
| 2 Job system | 2.1–2.5 | ☐ ☐ ☐ ☐ ☐ |
| 3 WP deploy | 3.1–3.5 | ☐ ☐ ☐ ☐ ☐ |
| 4 Rank tracking | 4.1–4.3 | ☐ ☐ ☐ |
| 5 Backlinks | 5.1–5.3 | ☐ ☐ ☐ |
| 6 Production | 6.1–6.4 | ☐ ☐ ☐ ☐ |

24 tasks total. At 2–3 tasks/day of focused Claude Code sessions, Phases
1–4 land in ~3 weeks, all six phases well inside the 3-month commitment
with buffer for the WordPress-deploy surprises the risk register predicts.
