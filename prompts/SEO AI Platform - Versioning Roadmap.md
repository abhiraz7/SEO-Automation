# VTechys SEO Platform — 3-Month Sprint Plan

Reconciles: the stakeholder MVP commitment (Abhinav's email), the original
V1–V11 Versioning Roadmap, and the actual code-audit findings. Sequenced by
real dependencies, not by roadmap order — several "MVP" items depend on
infrastructure the roadmap doesn't mention building until later.

---

## 0. Reconciliation — what "MVP" actually requires vs. what exists

| Committed MVP feature | Roadmap ref | Actual code state | Gap |
|---|---|---|---|
| Site Audit | V1 + V1.5 | **Done.** Crawler extracts every listed field; audit rules cover every listed category; 0–100 score. | Runs synchronously in-request — fine for MVP demo, not for concurrent production load (see Sprint 3). |
| AI Fix Suggestions | V3 + V4 | **Mostly done.** Claude generates suggestions, rule-validated. Generates 3 not 5 (config value, trivial fix). Regeneration deletes history. | Suggestion history not preserved — low risk for MVP unless the client demo specifically shows history. |
| WordPress Auto-Fix | V6 + V11 (both "Not Started") | **Does not exist.** No WP REST calls, no accept/reject fields on `Suggestion`, no deploy code, no rollback, no revision history. | **This is the real risk in the 3-month plan.** Everything else on the MVP list is "add a data source" work; this is "build a new subsystem with external-system writes and safety requirements" work. Treat as its own multi-sprint track, not a checkbox next to Keyword Research. |
| Keyword Research | Not on original roadmap at all | **Built, more mature than most roadmap items** — workspaces, dual-provider, trend tracking, scoring. But **actively broken**: silent provider-error normalization, hardcoded US location, DataForSEO blocked on account verification. | Needs a bug-fix pass, not a build. Cheapest item on this list to close. |
| Competitor Analysis / RivalFlow | V8 ("Not Started") | **Does not exist.** Zero matches in repo. | Full build. |
| Rank Tracking | Not its own roadmap version; `KeywordSnapshot.position` column exists but stays empty | **Storage exists, nothing populates it.** | Needs the fetch+write logic; schema is already there, which reduces scope. |
| Backlink Analysis | Sprint 9 on the original Sprint-numbered doc, not in V1–V11 | **Does not exist**, but Semrush's `backlinks_overview` is confirmed working (tested this week). | Full build, but the hardest part (provider access) is already proven. |
| Content Optimization | Not a distinct V-number; overlaps V3's suggestion engine | **Partially covered** by existing Suggestion engine, but no SERP-informed signal feeding it. | Extension of existing system, not new infrastructure. |

**Phase 2, correctly deferred by the stakeholder email and confirmed by the
audit as genuinely unstarted:** AI Visibility Score (V10), deep Security
Audit, RAG/Learning (V9, and V7's dead Supabase stub), GMB Ranking. Nothing
here changes that call — all four are zero-built and each has the kind of
open-ended scope (custom scoring models, historical data dependencies) that
doesn't belong in a 3-month commitment.

---

## 1. Sequencing logic (why this order, not roadmap order)

1. **Fix before you build on top of it.** Keyword Research is broken right
   now — building Competitor Analysis or Content Optimization on top of a
   keyword pipeline that silently returns fake-empty data just propagates
   the bug into two more features. Fix first.
2. **Foundation before features that need scheduling.** Rank Tracking and
   Backlink Analysis both need periodic (daily/weekly) data pulls, not
   one-shot fetches. Building each its own ad-hoc scheduler is wasted work.
   One scheduler, built once, serves both — plus Site Audit's own crawl
   scheduling need.
3. **The riskiest, least-proven item starts early, not last.** WordPress
   Auto-Fix has the most unknowns (staging validation, WP REST edge cases,
   rollback correctness) — starting it in month 3 with no runway to absorb
   surprises is how 3-month plans slip. Start the groundwork in month 1,
   in parallel with the Keyword Research fix track.
4. **Data-source features (Rank Tracking, Backlink Analysis, Competitor
   Analysis) share a provider pattern already proven by Keyword Research** —
   adapter → normalized result → three-outcome (`ok`/`no_data`/`error`)
   contract. Once that pattern's fixed in Keyword Research, each new
   feature is faster to build correctly the second, third, fourth time.

---

## 2. Sprint plan (twelve weeks, six two-week sprints)

If there's more than one engineer, Track A and Track B below can run in
parallel from Sprint 1; if solo, run them sequentially in the order listed.

### Sprint 1 (Weeks 1–2) — Stabilize the foundation

**Track A — Keyword Research bug fixes**
- Fix silent error normalization in `keyword_provider.py`/`dataforseo.py`
  (three-outcome `ok`/`no_data`/`error` contract)
- Fix hardcoded US location; thread location through both adapters
- Complete DataForSEO account verification, confirm real data returns
- Add `GET /keywords/provider-status` endpoint

**Track B — WordPress Auto-Fix groundwork (start early, it's the risk item)**
- Confirm WP REST API auth approach against `vseo.vtraffic.io` (app
  password vs. custom plugin — decide this now, it shapes everything after)
- Add `status` field (`pending`/`accepted`/`rejected`/`edited`/`deployed`)
  and timestamps to `Suggestion` model (this is V6, Acceptance Tracking —
  small schema change, unblocks everything downstream)
- Build accept/reject/edit endpoints in `routes/suggestions.py`

**Acceptance criteria:** a tracked keyword shows real volume/difficulty/
intent for an Indian-market query end-to-end; a suggestion can be marked
accepted/rejected and that state persists and is queryable.

---

### Sprint 2 (Weeks 3–4) — Crawl Engine foundation + WP write path

**Track A — Crawl Engine backend (`CrawlJob` model, queue, basic worker)**
- `CrawlJob` model + persistence
- Wire the existing Crawler Settings drawer UI to real settings storage
- Single background worker (not full pool yet) so crawls stop blocking
  the request — this alone fixes a real production risk before more
  load arrives

**Track B — WordPress Auto-Fix: single-field write**
- Implement the narrowest possible write: push a single accepted meta
  title/description fix to a **staging** WordPress install via REST API
- No bulk, no rollback yet — prove the write path works end-to-end for
  one field type before expanding scope

**Acceptance criteria:** an accepted meta-description suggestion, approved
in the UI, actually appears on the staging WordPress page. This is the
single most important proof point in the whole 3-month plan — get it real
early, not assumed.

---

### Sprint 3 (Weeks 5–6) — Scheduler + WP write expansion + Postgres

**Track A — Scheduler + discovery layer**
- Sitemap/nested-sitemap/robots.txt discovery
- 24h default schedule + hourly/daily/weekly options
- This scheduler is what Rank Tracking and Backlink Analysis will hang
  their periodic pulls on in Sprints 4–5 — build it generically, not
  crawl-specific

**Track B — WordPress Auto-Fix: expand field coverage + revision history**
- Extend the write path to headings, alt text, basic schema (per stakeholder
  scope)
- Store a revision record (before/after value, timestamp, which suggestion)
  for every deployed change — this is the minimum viable "revision history"
  the stakeholder email requires
- **Rollback**: restore the previous value from the revision record

**Also this sprint:** migrate SQLite → PostgreSQL. This is the point where
concurrent writes stop being theoretical — crawl workers and WordPress
deploy writes will be happening in the same window as normal user traffic.
Doing this now, on a clean base, is cheaper than doing it under load later.

**Acceptance criteria:** a deployed fix can be rolled back to its prior
value with one action; the app runs on Postgres with no data loss (verified
row-count match against the SQLite backup).

---

### Sprint 4 (Weeks 7–8) — Rank Tracking + Backlink Analysis (build)

**Track A — Rank Tracking**
- Populate `KeywordSnapshot.position` via scheduled Semrush/DataForSEO SERP
  position checks (daily or weekly per keyword, using Sprint 3's scheduler)
- Historical trend view reusing the existing trend-diffing pattern

**Track B — Backlink Analysis**
- Build per the spec already written: `BacklinkSnapshot` +
  `BacklinkRecord`, Semrush `backlinks_overview` (confirmed working) wired
  first, new/lost diffing, referring domains, anchor text distribution
- Toxic-link heuristic flagging (explicitly labeled as heuristic, not a
  verified score, in the UI)

**Acceptance criteria:** a tracked keyword shows a real position trend over
at least two scheduled checks; a project's backlink profile shows accurate
new/lost links after two scheduled pulls.

---

### Sprint 5 (Weeks 9–10) — Competitor Analysis (RivalFlow) + Content Optimization

**Track A — Competitor Analysis / RivalFlow**
- Input: your page + target keyword
- Fetch competitor pages ranking for that keyword (Semrush/DataForSEO SERP)
- Extract terms/questions/sections from competitor content, diff against
  your page
- Output: missing terms, missing questions, missing sections, heading/meta
  suggestions — simple dashboard view, matches stakeholder's stated scope

**Track B — Content Optimization**
- Extend the existing Suggestion engine with SERP-derived signals (reuse
  RivalFlow's competitor extraction as an input to `prompt_builder.py`,
  not a separate pipeline)
- Draft-level suggestions only, no auto-publish, matching stated MVP scope

**Acceptance criteria:** RivalFlow correctly identifies at least one real
content gap on a test page against a known competitor; a content
optimization suggestion visibly incorporates a SERP-derived signal (e.g.
a term the competitor covers that the current page doesn't).

---

### Sprint 6 (Weeks 11–12) — Hardening, security basics, production deploy

- Basic Security Audit checks per stakeholder's "Partial MVP" scope: SSL,
  security headers, robots.txt — these are cheap additions to the existing
  `audit.py` RULES list, not a new subsystem
- Full production deployment per the Windows VPS pipeline (NSSM services,
  Caddy reverse proxy, TLS, backups, external uptime monitoring)
- Full-checklist QA pass across every MVP feature, staging → production
  promotion for the WordPress write path specifically (test on staging
  first, exactly as the stakeholder email specifies, before any real
  client site gets a live auto-fix)
- Close remaining Sprint 1 debt: suggestion history (currently deleted on
  regeneration), suggestion count (3 → 5 if still desired)

**Acceptance criteria:** every item in §0's "Committed MVP feature" column
passes a real end-to-end test against the production deployment, not just
localhost.

---

## 3. What's explicitly NOT in this 3-month plan

Matches the stakeholder's Phase 2 list, confirmed correctly deferred by the
code audit (all four are genuinely zero-built, not partially done):

- **AI Visibility Score** (V10) — needs a custom scoring model combining
  rank/traffic/CTR/authority; no standard metric exists to shortcut this.
- **Security Audit (deep)** — vulnerability scanning beyond the basic
  SSL/headers/robots.txt checks in Sprint 6.
- **RAG / Learning** (V9, and the dead V7 Supabase stub) — genuinely
  blocked on having real acceptance data to learn from, which Sprint 1's
  Acceptance Tracking work starts collecting but won't have volume on
  within 3 months.
- **GMB Ranking** — location-based tracking complexity, correctly flagged
  as inconsistent by the stakeholder.
- **LLM Judge** (V5) — not in the stakeholder's MVP list at all; the
  original roadmap's "moat" vision (Issue + Suggestion + Judge Score +
  Visibility + Acceptance + Outcome) is intentionally not being pursued in
  this 3-month window. Worth being explicit with Abhinav that this means
  the long-term moat described in the original Versioning Roadmap is
  deferred, not being built toward incrementally — Acceptance Tracking is
  the only piece of that vision landing in this plan.

---

## 4. Risk register (things that can blow the 3-month timeline)

- **WordPress Auto-Fix is the highest-variance item.** WP REST API
  behavior varies by plugin ecosystem, hosting environment, and theme;
  "works on `vseo.vtraffic.io`" doesn't guarantee "works on an arbitrary
  client's WordPress install." Budget for this being the sprint that slips,
  not the one that finishes early.
- **DataForSEO account verification is external and un-schedulable** — it
  was still blocked as of the last check. If it stays blocked, Rank
  Tracking and Backlink Analysis fall back to Semrush-only, which is
  workable (Semrush is confirmed fully functional) but narrows redundancy.
- **Postgres migration in Sprint 3 is a hard dependency for Sprint 4–5's
  concurrent scheduled writes** — if Sprint 3 slips, Sprint 4 either slips
  with it or ships on SQLite with the lock-contention risk already flagged
  in this conversation.
- **Competitor Analysis and Content Optimization (Sprint 5) both depend on
  Rank Tracking/Backlink infrastructure being real from Sprint 4** — a
  slip in Sprint 4 cascades directly into Sprint 5, there's no independent
  path around it.
