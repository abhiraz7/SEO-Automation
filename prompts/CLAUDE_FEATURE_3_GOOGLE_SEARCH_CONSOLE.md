# Claude Code Task List — Feature 3: Google Search Console Integration

## Source

Design rationale: `prompts/Google-serch-console.md` (read that first — this file is the
**sequential build plan** derived from it, broken into the same small, independently
verifiable steps Feature 1 and Feature 2 used).

## Ground rules (carried over from Feature 1 & 2 — see `CLAUDE.md`, `feedback_task_list_workflow`)

- **One task at a time.** Implement exactly one numbered task below, stop, the owner verifies
  it, then commit + push, then move to the next. Do not chain tasks in one pass.
- **`ok` / `no_data` / `error` on every GSC call**, same discipline as `semrush.py` /
  `dataforseo.py`. "No rows yet" is `no_data`, not `error`.
- **GSC must never be a hard dependency.** If a project has no GSC connection, or a call
  fails, every existing feature (deploy, suggestions, competitor gap, optimizer) must keep
  working exactly as it does today. GSC only adds information when available.
- **New tables only** — created via `Base.metadata.create_all` at startup, no Alembic
  migration, same convention Feature 2's optimizer tables used.
- **`app/models.py` changes are typed by the owner, not written by Claude** — Claude explains
  the schema and where it goes; the owner types it in and pastes it back for review (standing
  project rule for schema/infra files).
- **Every failure logged once**, at the point the outcome is decided, via
  `app/services/failure_log.py` (`failure()` / `crash()`), matching the existing project-wide
  logging rule.
- **Same TDD + mutation-testing discipline as Feature 1/2**: tests per module as it's built,
  then a handful of targeted mutations per risky module to prove the tests actually catch
  regressions, before moving on.
- **Never push to `main`** without the owner's explicit go-ahead.

## Explicitly deferred (per the source doc's own advice — do not build these in V1)

- BigQuery bulk export
- Indexing API for ordinary pages (restricted to `JobPosting` / `BroadcastEvent` by Google)
- A full clone of the Search Console UI
- Daily/scheduled pulling of every query for every page — snapshots stay targeted (manual +
  around approved changes), not a crawler

---

## Task 0 — Prerequisite: Google Cloud OAuth app (owner, not code)

Create a Google Cloud project, configure the OAuth consent screen, and create a **Web
application** OAuth Client (Client ID + Secret). Register redirect URIs for both local dev
(`http://localhost:8000/gsc/callback` or whatever port is used) and the production domain
(`https://<prod-domain>/gsc/callback`). Nothing in Phase 1 can be tested end-to-end without
this — same kind of hard external blocker as the SEMrush Projects-cap issue was for Site
Audit.

**Know this going in (confirmed via research):** while the OAuth consent screen sits in
"Testing" publishing status (the default), refresh tokens issued to it **expire after 7
days**, so the connection will silently need reconnecting weekly during development. That's
expected, not a bug — decide later whether to submit for verification/"In production" status
once the feature is stable enough to matter.

**Verify:** you can see the Client ID/Secret in Google Cloud Console and both redirect URIs
are listed under the OAuth client's "Authorized redirect URIs."

---

## Phase 1 — Connect (OAuth + property selection)

Corresponds to the source doc's §17–§20, "Stage 1."

### Task 1 — Dependencies + config
- Add `google-auth`, `google-auth-oauthlib`, `google-api-python-client`, `cryptography` to
  `requirements.txt` (check first whether any are already present).
- Add env vars: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`,
  `GOOGLE_OAUTH_REDIRECT_URI`, `GSC_TOKEN_KEY` (Fernet key, same naming convention as the
  existing `WP_TOKEN_KEY`). Document all four in `.env.example` alongside the existing
  entries.
- **Verify:** app boots locally with the new env vars set; a missing/blank
  `GOOGLE_OAUTH_CLIENT_ID` degrades to "GSC not configured" rather than crashing.

### Task 2 — `google_connections` + `search_console_properties` models
- New tables, one Google account connection per project, one row per discoverable property
  under that connection (`site_url`, `permission_level`, `selected`).
- Owner types this into `app/models.py` (teaching-mode); Claude explains the shape and why it
  mirrors the doc's §24 schema.
- **Verify:** on a fresh boot (no existing DB file, same check Feature 2 did for CI-safety),
  both tables get created automatically.

### Task 3 — `app/google_search_console.py` (new provider module)
- OAuth helpers: `build_auth_url(state)`, `exchange_code_for_tokens(code)`,
  `refresh_access_token(refresh_token)` — force `access_type=offline&prompt=consent` on the
  auth URL so a refresh token is actually returned (Google only issues one on first consent
  otherwise). Use the `google_auth_oauthlib.flow.Flow` class (not `InstalledAppFlow`, which is
  for desktop apps), reconstructed fresh in both `/gsc/connect` and `/gsc/callback` — don't try
  to keep the `Flow` object alive between requests, only the opaque `state` string needs to
  round-trip through the session.
- **One service object for all four APIs**: `build('searchconsole', 'v1', credentials=creds)`.
  The old `build('webmasters', 'v3', ...)` name is retired from Google's live discovery
  service (still limps along via a bundled static cache, but don't use it) — `searchconsole`
  v1 alone exposes `.sites()`, `.sitemaps()`, `.searchanalytics()`, and
  `.urlInspection().index()`. No need for two separate client objects.
- `list_sites(access_token)` wrapping `service.sites().list().execute()`, ok/no_data/error
  return shape, same pattern as `semrush.py`'s functions. **Gotcha**: the response field is
  `siteEntry` (an array), not `sites` — easy to get wrong on a first pass. Each entry's
  `permissionLevel` value casing is unconfirmed (Google's discovery doc says
  `SITE_OWNER`/`SITE_FULL_USER`/etc., independent real-world examples say lowerCamelCase
  `siteOwner`/`siteFullUser`/etc.) — store `site_url` and `permission_level` as plain string
  columns for v1, log the actual value from the first real response, and only consider a
  `CHECK`/enum constraint after that's confirmed.
- **Re-persist credentials after every call, not just on explicit refresh.** Google's
  `Credentials` object mutates itself in place on any auto-refresh triggered mid-call (not
  only when we call `.refresh()` ourselves) — so every function in this module that makes a
  Google API call must re-encrypt and save `creds.token`/`creds.expiry` back to
  `google_connections` afterward, or the DB can end up holding a stale access token while
  Google has already rotated it. Treat `invalid_grant` on a refresh attempt as "connection
  revoked" (a distinct status the UI shows as "reconnect needed"), not a transient error to
  retry.
- Small encryption helper (`encrypt_token` / `decrypt_token`) — **mirror
  `app/wordpress.py`'s existing `_get_fernet()` / `encrypt_token()` / `decrypt_token()` exactly**
  (lines 39-61: reads a Fernet key from an env var, raises a clear `RuntimeError` with the
  keygen command if unset, `InvalidToken` on decrypt means the key changed), just pointed at
  `GSC_TOKEN_KEY` instead of `WP_TOKEN_KEY`. This is a proven pattern already running in
  production for WordPress tokens — no new approach needed.
- **Verify:** unit tests with mocked HTTP responses (mirroring how `test_semrush.py` /
  `test_dataforseo.py` mock their providers) — token exchange, refresh, and site listing, each
  for the ok/no_data/error paths.

### Task 4 — `app/routes/search_console.py`
- `GET /gsc/connect` — builds the auth URL (with a signed/session `state` value for CSRF
  protection) and redirects to Google.
- `GET /gsc/callback` — verifies `state`, exchanges the code, stores the encrypted connection,
  calls `list_sites`, stores discovered properties.
- `POST /gsc/properties/{id}/select` — marks one property as the active one for the project.
- `POST /gsc/disconnect`.
- **Verify:** route tests with mocked OAuth exchange + Sites API (state mismatch is rejected
  with 400); then one real manual click-through against Google once Task 0 exists.

### Task 5 — Minimal connect/property-picker UI
- A "Google Search Console" card on the **project** page: not-connected state with a Connect
  button; connected state showing the account email, the list of discovered properties with
  permission level, and a select action — following the visual pattern of the existing
  **per-project WordPress connection card** (not the global `/settings` page, which only
  controls the DataForSEO-vs-SEMrush provider switch — a different, app-wide setting, not a
  per-project one).
- **Verify:** Jinja render test with seeded connected/not-connected/multi-property fixtures;
  one real browser click-through of the full connect flow.

*(Phase 1 alone is a complete, demoable slice: "Connected as you@gmail.com, property
selected" — stop and verify before Phase 2.)*

---

## Phase 2 — Performance data

Corresponds to §2–§8, "Stage 2."

### Task 6 — `gsc_performance_snapshots` + `gsc_query_performance` models
- One row per (page, date range) pull; query-level child rows carry query/country/device/
  search_appearance breakdowns for that snapshot.
- Owner types into `app/models.py` (teaching-mode).
- **Verify:** tables auto-create; a quick manual insert/read round-trip.

### Task 7 — `fetch_performance()` in the provider module
- Wraps `service.searchanalytics().query(siteUrl=..., body={...}).execute()`. Request fields:
  `startDate`/`endDate` (`YYYY-MM-DD`), `dimensions` (subset of `date`/`page`/`query`/
  `country`/`device`/`searchAppearance`/`hour`), `rowLimit` (default 1000, **max 25,000**),
  `startRow` (pagination, in 25,000-row increments), `dataState` (`final` = fully-processed
  only, default; `all` = includes fresher/partial data; `hourly_all`).
- Response `rows` may be **absent or an empty array — this is success, not an error** (map
  `ok`/`no_data` off HTTP status + presence of `rows`, not by parsing anything). A real error
  (bad dates, unverified site, no permission, quota) comes back as a non-2xx status raised by
  `googleapiclient` as `HttpError`. Each row's `keys[]` lines up positionally with the
  `dimensions` you requested; `ctr` is a **0.0–1.0 fraction, not a percentage** — store the raw
  fraction, multiply by 100 only at render time.
- Carry a `truncated: bool` when the row count hits the request's `rowLimit` (per §16 — never
  claim "these are all queries"). There's also a hard ceiling independent of pagination —
  Google documents roughly **50,000 total rows per property per search type per day** — worth
  a code comment since it's the concrete number behind that caveat. Data is typically available
  2-3 days after the fact; there's no fixed SLA, so don't promise an exact freshness window in
  the UI.
- **Verify:** unit tests with mocked Search Analytics responses covering ok, no_data (query
  succeeded, zero/absent rows — very common for low-traffic pages), error (auth/quota), and
  truncated.

### Task 8 — Manual refresh + 28-day summary UI
- A "Refresh performance" button on a page's detail view; stores a snapshot; shows 28d
  clicks/impressions/CTR/position, matching §23's card layout.
- **Verify:** route + render tests; one real pull against a connected property.

### Task 9 — Query / device / country breakdown
- Reuses the snapshot from Task 8's pull; renders the query table (§3–§5) and device/country
  tables (§6–§7) from `gsc_query_performance` rows already stored, discovering
  `search_appearance` values dynamically rather than hardcoding a list (per §8).
- **Verify:** render tests with fixtures covering multiple devices/countries/appearance types.

---

## Phase 3 — Scheduled snapshots

Wires Phase 2 into the existing job/handler registry so pulls aren't purely manual.

### Task 10 — `app/jobs/handlers/gsc_snapshot.py`
- Mirrors the shape of `app/jobs/handlers/audit.py`; on schedule, pulls performance for every
  page with an active GSC connection and stores a snapshot, using the same APScheduler
  registration pattern the audit job uses.
- **Verify:** unit test that manually invokes the handler against seeded connections/pages;
  confirms `failure_log.failure()` fires once (not per-page) on a Google API error, and that
  the happy path logs nothing at WARNING (same proof style as `feedback_log_every_failure`).

---

## Phase 4 — URL Inspection

Corresponds to §9–§12, "Stage 3."

### Task 11 — `inspect_url()` in the provider module
- Wraps `service.urlInspection().index().inspect(body={"siteUrl": ..., "inspectionUrl": ...}).execute()`
  — same `searchconsole` v1 service object as everything else, no separate client. Response is
  one `inspectionResult` object containing `indexStatusResult` (always present) plus
  `ampResult` (absent if not AMP) and `richResultsResult` (absent if nothing detected).
  `mobileUsabilityResult` exists in the schema but is **documented deprecated and no longer
  populated** — don't build anything expecting real data there.
- `indexStatusResult` fields, all confirmed against a real example response: `verdict`
  (`PASS`/`PARTIAL`/`FAIL`/`NEUTRAL`), `coverageState` (**free text like "Submitted and
  indexed", not a closed enum**), `robotsTxtState` (`ALLOWED`/`DISALLOWED`), `indexingState`
  (`INDEXING_ALLOWED`/`BLOCKED_BY_*`), `pageFetchState` (`SUCCESSFUL`/`SOFT_404`/etc.),
  `lastCrawlTime`, `googleCanonical`/`userCanonical`, `sitemap` (**array, despite the singular
  field name**), `referringUrls` (array), `crawledAs` (`DESKTOP`/`MOBILE`). These enum values
  are independently confirmed real (unlike Sites/Sitemaps below), safe to store as constrained
  strings.
- `ampResult`/`richResultsResult` are variable-depth nested structures (rich results in
  particular can have several detected types, each with its own items/issues) — store as JSON
  columns for v1 rather than normalizing into child tables, matching the
  `rich_results_json`/`raw_response_json` columns already sketched below.
- **Verify:** unit tests with mocked URL Inspection responses covering the AMP-absent and
  rich-results-absent cases as well as the full case.

### Task 12 — `gsc_url_inspections` model
- Owner types into `app/models.py`. Plain string columns for `verdict`/`coverage_state`/
  `robots_txt_state`/`indexing_state`/`page_fetch_state`/`crawled_as` (confirmed real values,
  but no need for DB-level enum constraints in v1), `sitemap_json`/`referring_urls_json` as
  JSON arrays, `rich_results_json`/`amp_result_json`/`raw_response_json` as JSON blobs.
- **Verify:** auto-creates.

### Task 13 — "Inspect URL" UI tied into deploy verification
- An Inspect-URL action on a page's detail view / post-deploy panel, displayed **separately**
  from the existing live-HTTP-check step already used in the deploy/rollback flow (per §10 —
  Google's indexed view is not the same as our live check; never conflate the two labels).
- No "Send for indexing" button anywhere (per §11/§12) — only "Inspect URL" and "Open Search
  Console," consistent with what Google actually supports for ordinary pages.
- **Verify:** render test confirming the two states are labeled distinctly; one real
  inspection call against a connected property.

---

## Phase 5 — Change outcome loop (the actual payoff — §14, §15, §21, §22, "Stage 4")

### Task 14 — `seo_change_outcomes` model
- Links `suggestion_id` → `baseline_snapshot_id` / `early_snapshot_id` (7d) /
  `stable_snapshot_id` (28d) / `long_term_snapshot_id` (90d).
- Owner types into `app/models.py`.
- **Verify:** auto-creates.

### Task 15 — Auto-baseline on suggestion deploy
- Hooks into the existing point where a `Suggestion` transitions to `deployed` (the same spot
  Feature 2's live-verification step runs). If the project has an active GSC connection,
  capture a baseline `gsc_performance_snapshots` row for that page immediately; if not
  connected, or the call fails, log via `failure_log` and continue — deploy must never be
  blocked or delayed by this.
- **Verify:** tests covering connected/not-connected/API-error paths, proving deploy behavior
  is unchanged in all three; mutation-test the "never blocks deploy" guarantee specifically.

### Task 16 — Backfill snapshots + before/after view
- A scheduled check (reusing Task 10's job) that, for outcomes past 7/28/90 days since
  `applied_at`, pulls the corresponding snapshot and fills in `early_snapshot_id` /
  `stable_snapshot_id` / `long_term_snapshot_id`.
- A "Change outcome" view per §14/§23: before → after clicks/impressions/CTR/position, labeled
  **"post-change performance"** with explicit date ranges — never "AI increased rankings by
  X%" (per §2's observational-not-causal caveat).
- **Verify:** tests for the backfill timing logic (7/28/90-day boundaries) and for the
  labeling/caveat text actually rendering; one real change tracked end-to-end once enough real
  days have passed.

---

## Phase 6 — Sitemaps (optional, do last)

Corresponds to §13. Lowest priority — only build if Phases 1–5 have already proven valuable.

### Task 17 — Sitemaps API + UI card
- `list_sitemaps()` / `submit_sitemap()` in the provider module, via
  `service.sitemaps().list(siteUrl=...)` / `.submit(siteUrl=..., feedpath=...)`. **Gotcha**:
  the parameter is `feedpath`, not `sitemapUrl`; the list response field is `sitemap`
  (singular, holds the array — same pattern as Sites API's `siteEntry`). Fields: `path`,
  `lastSubmitted`, `lastDownloaded`, `isPending`, `isSitemapsIndex`, `type` (casing
  unconfirmed like `permissionLevel` — store as a plain string), `warnings`/`errors` (counts
  only, no per-item detail text available from this API), `contents[]` (per-type
  `submitted`/`indexed` counts — but `indexed` is documented deprecated and not actually
  populated, don't build UI expecting it).
- A simple card showing submitted sitemaps, last downloaded, warnings/errors counts, with a
  submit action.
- **Verify:** unit tests with mocked Sitemaps API responses; one real listing against a
  connected property.

---

## Data model reference (for context — not all created at once; each table is created in the
task above that first needs it)

| Table | Created in |
|---|---|
| `google_connections` | Task 2 |
| `search_console_properties` | Task 2 |
| `gsc_performance_snapshots` | Task 6 |
| `gsc_query_performance` | Task 6 |
| `gsc_url_inspections` | Task 12 |
| `seo_change_outcomes` | Task 14 |

## Open items to confirm before Task 1 starts

1. Task 0 (Google Cloud OAuth client) — done or not yet?

## Parked (2026-09-29) — prod domain + TLS

Discovered while doing Task 0: Google's OAuth redirect URI validation rejects a bare IP
(`http://54.80.253.215/gsc/callback`) outright -- "Must end with a public top-level domain".
Prod currently has no domain name and no TLS termination in front of the app at all, so this
blocks a prod redirect URI, not just GSC specifically -- any future OAuth-based integration
would hit the same wall.

Task 0 was completed with **localhost only** registered as the redirect URI, so local dev/
testing is unblocked. The prod side is explicitly **paused, not solved** -- picked up later.

Leading option when this resumes: point a subdomain of vtechys.com (candidates: `app.
vtechys.com`, `seo.vtechys.com`) at 54.80.253.215 via an A record, then add Caddy in front of
the existing Docker Compose app for automatic Let's Encrypt TLS. Needs: which subdomain, and
where vtechys.com's DNS is managed (owner said "Other" when asked, not yet identified).
Alternatives considered: an AWS ALB + ACM cert (more AWS-native, more moving parts), or a free
dynamic-DNS domain (fast but unprofessional-looking for a product being resold to agencies --
ruled out for that reason).

Until this resumes, GSC in prod stays disconnected -- the app already degrades gracefully
(`is_configured()` returns False, nothing crashes) rather than needing this to ship the rest
of the feature.

## Research complete (both background agents finished, cross-checked against Google's live
discovery document, not just doc pages)

- `webmasters.readonly` alone covers every read operation across all four APIs (Sites
  list/get, Search Analytics query, Sitemaps list/get, URL Inspection) — the write scope
  `webmasters` is only needed for `sites.add`/`sites.delete`/`sitemaps.submit`/
  `sitemaps.delete`, confirming the doc's staged-scope plan (§17) works as described.
- Three field names across the legacy resources have ambiguous real-world casing (Sites'
  `permissionLevel`, Sitemaps' `type`) that the plan above now stores as plain strings rather
  than guessing an enum. URL Inspection's enums, by contrast, are independently confirmed real
  and safe to constrain.
- Two "singular field name holding an array" traps are called out inline above
  (`siteEntry`, `sitemap`) since they're exactly the kind of thing that passes a quick glance
  and fails at runtime.

## Resolved while grounding the plan against real code

- **Token encryption**: no longer an open question — `app/wordpress.py` already has a working
  Fernet pattern (`WP_TOKEN_KEY` env var, `_get_fernet()`, `encrypt_token()`/`decrypt_token()`,
  raises `RuntimeError` with the keygen command if unset). Task 3 mirrors it exactly with
  `GSC_TOKEN_KEY`. Confirmed real test access exists to validate against: the connected Google
  account already has Search Console access to `https://vtechys.com/` (owner/full-user level),
  so once Task 1 ships, that's the first property to connect and verify against.

## Confirmed decisions

- **Connection scope: project-level, not a separate shared "Google account" entity.**
  `google_connections`/`search_console_properties` are keyed by `project_id`, same as the
  existing WordPress connection — one Project = one website = one GSC connection. This sits
  in the UI as a section inside a Project (alongside Overview/Audit/Competitors/WordPress),
  not as a top-level nav item. A GSC "property" (domain vs URL-prefix) is a different concept
  from our "Project"; Task 5's property picker is what reconciles the two, not the connection
  scope itself. Reusing one Google account's connection across multiple projects (useful for
  agencies) was raised and explicitly **rejected** on 2026-09-29 — strictly one connection per
  project, no shared/reusable connection entity, no exceptions. Don't revisit this without the
  owner explicitly reopening it.

- **Who authorizes the OAuth connection, per project (decided 2026-09-29).** The same OAuth
  code path (Flow 2's mechanics — a real Google login completes real consent) is used
  regardless of *whose* Google account does it. Per project, whichever Google account already
  has Search Console access to that property is the one that connects:
  - Agency-owned sites (e.g. vtechys.com) → connect with the agency's own Google account.
  - Client sites → **SOP: the client adds the connecting account as a GSC user first**
    (Search Console → Settings → Users and permissions → Add user → **Full user** level, not
    Owner, not Restricted — Full user covers every read this feature needs, since
    `webmasters.readonly` scope requests don't need Owner-level access; Restricted user's report
    coverage is documented as limited, so avoid it for the connecting account), **then** that
    account does the OAuth connect on the project page. This is the standard "add agency as a
    GSC user" pattern already used for GA4/Ads, done once per client relationship — not a new
    process either the client or the agency has to learn.
  - This keeps every OAuth consent screen click on an account your own team controls (or the
    client's own account when the client prefers to connect it directly) — never a shared or
    reused credential.
  - **Why this over "client clicks Connect with their own account" as the default**: while the
    OAuth consent screen is in Testing status (see Task 0), every account that will complete a
    real consent must be pre-added as a test user in Google Cloud Console, and the resulting
    refresh token expires after 7 days. Routing connects through accounts the agency controls
    means *the agency* notices and reconnects on that cadence, rather than each client silently
    losing their connection and not knowing why. Revisit "client connects directly" as the
    default once the OAuth app is verified/"In production" — at that point the 7-day expiry and
    the "unverified app" warning both go away, and the extra friction of the Full-user-grant SOP
    step may no longer be worth it.
  - **No data difference either way.** Search Console data belongs to the *property*, not the
    viewing account — clicks/impressions/CTR/position/index status are identical regardless of
    which authorized account (agency's, client's, or yours) makes the API call, as long as the
    account holds at least Full user access. Only *write* actions (submit sitemap, manage users,
    delete property) are gated by permission level; every read this feature does is not.
  - **Verified against two real accounts during design (2026-09-29)**: agency account has
    Owner access to `https://vtechys.com/`; the owner's personal account has Owner access to
    `https://examnotespdf.in/` (plus one *unverified* property that isn't usable via the API
    until verified in Search Console directly — verification status is unrelated to and prior
    to OAuth access). Both accounts need to be added as OAuth consent screen test users during
    Task 0 since both will do real connects during development.

## Implementation pacing (decided 2026-09-29)

- **Teaching mode is dropped for this feature.** Per standing CLAUDE.md convention, Claude
  normally explains infra/schema files and has the owner type them in. For Feature 3 the owner
  asked Claude to write everything directly — including `app/models.py` changes and
  `.github/workflows/deploy.yml` — with no typing-checkpoint pauses. This is a one-time,
  feature-scoped exception; it does not change the standing CLAUDE.md rule for other work.
- **"One task at a time, stop for verification" is relaxed to "full send."** Claude implements
  through as many tasks as are unblocked, back-to-back, committing as it goes, without pausing
  to wait for the owner's verification between each one. The owner reviews the accumulated diff
  afterward rather than task-by-task. The `ok`/`no_data`/`error` discipline, per-module tests,
  and mutation-testing spot checks from the Ground rules above still apply — only the
  stop-and-wait cadence is relaxed, not the quality bar.
- **Never push to `main` without explicit go-ahead** (Ground rules, above) still applies
  unchanged — "full send" means multiple local commits on `feature/google-search-console` in
  one sitting, not skipping the final push approval.
