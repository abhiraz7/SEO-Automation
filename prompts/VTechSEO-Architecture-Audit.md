# VTechSEO Architecture Audit

Produced per `prompts/Backlink-Analysis-Audit-and-Plan.md`'s brief. No code
changed while writing this — every claim below was checked against the
actual repository (grep/read), not assumed. Where I couldn't verify
something live (e.g. real API cost), I say so rather than guess.

---

## 1. What exists today

A FastAPI app (`app/main.py`) with 10 routers, SQLite persistence, a
subprocess-based job scheduler, and three external provider integrations
(SEMrush, DataForSEO, DataForSEO On-Page). Two LLM providers (Claude,
Gemini) were wired in as of today via a shared dispatcher. No `providers/`,
`analysis/`, or `ai/` package structure exists — everything currently lives
as flat modules directly under `app/`.

## 2. What already works (verified live or via real data in the DB today)

- **Keyword Research**: `keyword_provider.py` — Semrush primary, DataForSEO
  fallback, honest `ok`/`no_data`/`error` states (`NormalizedKeyword` in
  `schemas.py` already matches the brief's "normalized data layer"
  requirement almost field-for-field).
- **Rank Tracking**: scheduled `rank_check` job populates `KeywordSnapshot`.
- **Backlink Analysis (partial)**: `BacklinkSnapshot` (overview) +
  `BacklinkRecord` (per-link, new/lost/active diffing) — Semrush-only,
  currently **broken live** (verified today: 403, "0 API units remaining"
  on the Semrush account — an account/billing issue, not a code bug).
- **Site Audit**: migrated to SEMrush Site Audit API / DataForSEO On-Page
  (crawler deliberately retired — matches this brief's explicit
  instruction not to reintroduce Crawl4AI).
- **AI Fix Suggestions + Meta Optimization**: `claude.py` (proven) +
  `gemini.py` (built today, live-tested, works once billing was enabled
  on the Gemini side) behind `ai_provider.py`, a provider-neutral
  dispatcher with a `/settings` toggle. **This already satisfies the
  brief's "Claude/Gemini interchangeable, provider-neutral interface"
  requirement** — built before this brief was received, same shape it
  asks for independently.
- **AI Visibility Score**: `visibility.py` + `VisibilityCheck` — real
  DataForSEO AI Overview citation + organic rank checks, live.
- **Job scheduler**: `app/scheduler.py` + `app/jobs/registry.py` — exactly
  the "modular job types, one registry, no giant job" pattern the brief
  asks for. Current handlers: `crawl`, `rank_check`, `keyword_refresh`,
  `backlink_pull`, `audit`.

## 3. What is partially implemented

- **Keyword clustering**: exists (`routes/keywords.py:_cluster_keywords`),
  but is explicitly documented as **lexical root-word only** — the exact
  thing this brief says not to rely on alone ("group keywords by their
  longest non-stopword token... no ML/embedding clustering — out of scope
  per plan"). SERP-based clustering (keywords sharing overlapping SERPs)
  does not exist.
- **Backlink Analysis**: 3 of 4 client-required capabilities done
  (referring domains, count, anchor text); competitor gap analysis is
  the missing piece, already scoped in the companion doc.
- **Error-state discipline**: exists and is good for Keyword Research and
  Backlinks (`ok`/`no_data`/`error` on `NormalizedKeyword` and
  `BacklinksOverview`) but is **not consistently applied everywhere** —
  e.g. `OnPageTask.status` uses `posted`/`fetched`/`error` (a different,
  ad-hoc vocabulary, not the same three-state contract).

## 4. What is missing entirely

- **Competitor model** — zero persistent storage anywhere. The only
  `competitor_domains` field in the whole schema
  (`VisibilityCheck.competitor_domains`, `models.py:432`) is auto-derived
  per AI-Overview-query, not a user-managed list. Sidebar nav even already
  has a disabled "Competitor Analysis" placeholder link
  (`partials/sidebar.html:80`, `enabled=False`) — the UI slot exists, the
  feature doesn't.
- **Competitor Keyword Gap** — no code.
- **Competitor Backlink Gap** — no code (this is the brief's named
  priority; already scoped in the companion plan doc, not yet built).
- **SERP Intelligence deterministic analysis** (weak/duplicate/forum/PAA
  detection etc.) — SERP data is fetched (`fetch_serp` in both provider
  modules) but nothing analyzes it beyond raw feature flags
  (`summarize_serp_features`). No classification layer.
- **Opportunity Engine / scoring across tools** — `keyword_scoring.py`
  scores individual keywords (volume/difficulty/intent/SERP components,
  already explainable with named factors — matches the brief's "no
  mysterious AI score" requirement for that one tool), but there's no
  cross-tool opportunity engine that aggregates keyword + backlink +
  on-page signals into one prioritized list.
- **AI reasoning layer for recommendations** (the `LLMResult` /
  evidence-package / Accept-Reject-Edit-Save-Ignore contract this brief
  describes) — doesn't exist yet. Current AI usage (suggestions, meta
  optimization, keyword briefs) is single-purpose, not a general
  reasoning/recommendation contract.
- **RivalFlow / Content Optimization** — confirmed zero code, per the
  earlier Master Sprint Plan audit.

## 5. What should NOT be rebuilt

Per the brief's own instruction, and confirmed working:
- `keyword_provider.py`'s primary/fallback provider pattern
- `ai_provider.py`'s Claude/Gemini dispatcher (already exists in the shape
  this brief asks for)
- The `Job`/`Schedule`/`JOB_HANDLERS` registry scheduler
- `BacklinkRecord`'s new/lost/active diffing logic
- `NormalizedKeyword` / `BacklinksOverview`'s ok/no_data/error schema
  pattern — this is the template to extend to new normalized types
  (`NormalizedBacklink`, `NormalizedReferringDomain`), not replace

## 6–9. Current provider/AI capabilities (see §2 for detail; summary table)

| Capability | SEMrush | DataForSEO | Claude | Gemini |
|---|---|---|---|---|
| Keyword overview/bulk/related/questions | ✅ | ✅ | – | – |
| SERP fetch | ✅ | ✅ | – | – |
| Backlinks overview/list | ✅ (currently 403 — 0 units) | ❌ not wired | – | – |
| On-page audit | ✅ (Site Audit API) | ✅ (On-Page API) | – | – |
| Suggestion/meta generation | – | – | ✅ | ✅ (built today) |
| Domain intersection (competitor gap) | unchecked | ❌ not built | – | – |

## 10. Keyword Research coverage vs. the brief's 17-item list

Have: Overview, Bulk, Related, Questions, basic SERP fetch, lexical
clustering, opportunity scoring (single-keyword), AI keyword briefs.
Missing: Long-tail as a distinct capability, Keyword Lists (beyond
`SavedKeyword`/workspaces — worth checking if this already covers it),
Competitor Keyword Gap, Local (location support exists — `keyword_locations.py`
— but "Local Keyword Research" as its own capability isn't distinct from
this), Keyword Trends (partial — `compute_trend` exists on snapshots),
SERP-based clustering, full SERP Feature analysis (flags exist, no
classification).

## 11. Backlink coverage vs. the brief

Have: referring domains, count, anchor text, new/lost/active tracking.
Missing: everything under "Backlink Gap" (§ Competitor Backlink Gap above)
— competitor discovery, competitor list management, domain intersection,
bulk referring-domain analysis, link opportunity scoring, AI
interpretation. Also currently non-functional live (Semrush 0 units).

## 12. Competitor architecture

None. This is the single most-repeated gap across every unbuilt feature
(RivalFlow, Backlink Gap, Keyword Gap all need the same competitor list
and none of them have it) — confirms the brief's instruction to build
**one** shared competitor model, not three.

## 13. Scheduler architecture

Solid, matches the brief's requirements as-is: modular handlers, one
registry, `Job`/`Schedule` tables, lane-based execution (crawl jobs
separated from "light" jobs so a slow crawl can't starve a rank check —
this was a real bug found and fixed earlier, per `AgentLog.md`). Adding
`competitor_backlink_pull` or `opportunity_analysis` job types is a
one-entry addition to `JOB_HANDLERS`, no scheduler rework needed.

## 14. Database architecture

SQLite, as the brief says to keep unless there's a concrete reason not
to — correct call, no migration needed. Models are reasonably normalized
already (see full class list, §2/§4). Gaps specific to this brief's ask:
no `Competitor` table, no `NormalizedBacklink`/`NormalizedReferringDomain`
tables (current `BacklinkRecord`/`BacklinkSnapshot` are close but
Semrush-shaped, not provider-neutral), no `Opportunity` or
`AIRecommendation` tables.

## 15. UI architecture

Sidebar (`partials/sidebar.html`) already has the exact information
architecture the brief describes — Projects, Keyword Research, Backlinks,
Site Audit (via project detail), AI Visibility, Settings — plus disabled
placeholders for **Competitor Analysis**, **Reports**, **Schema
Generator**, **Link Analyzer**, **AI Writer**. The nav slots for future
features already exist; several of this brief's asks would fill an
existing disabled link rather than need new navigation design.

## 16. Technical debt relevant to this brief

- Inconsistent status vocabularies across features (`ok/no_data/error` vs
  `posted/fetched/error`) — worth deciding whether to unify before adding
  more provider integrations that would otherwise invent a third
  vocabulary.
- `Suggestion` has no `source` column — can't tell which AI provider
  generated a historical suggestion (flagged in code today when Gemini
  was added; not fixed, scope was kept tight at the time).
- No `analysis/` layer exists at all — every "deterministic analysis"
  the brief wants (SERP classification, opportunity scoring across tools)
  would be genuinely new code, not a refactor of existing logic.

## 17. Recommended LEGO modules (only for gaps confirmed above — not a rebuild list)

- `app/models.py`: add `Competitor` (shared across Keyword Gap, Backlink
  Gap, RivalFlow — per brief §COMPETITOR MODEL)
- `app/dataforseo.py`: add `fetch_domain_intersection`,
  `fetch_bulk_referring_domains` (already scoped in the companion
  Backlink plan doc)
- `app/jobs/handlers/competitor_backlink_pull.py`: mirrors
  `backlink_pull.py`'s existing diff pattern
- `app/analysis/` (new package): starts with `backlink_analysis.py`
  (deterministic gap ranking — competitors-linked count, authority,
  before any AI touches it, per brief's FACTS→ANALYSIS→AI separation)

## 18. Recommended build order

1. **Competitor model** (shared table) — everything else depends on this,
   build once.
2. **DataForSEO backlink functions** (`domain_intersection` +
   `bulk_referring_domains`) — also fixes today's "Semrush backlinks 403"
   outage by giving backlinks a second provider, not just enabling gap
   analysis.
3. **Deterministic backlink-gap analysis** (Python, no AI) — count of
   competitors linked, authority, rank the gap list — per brief's
   explicit "never use an LLM for basic detection" rule.
4. **AI interpretation layer on top** (HIGH/MEDIUM/LOW classification +
   explanation) — only after 1–3 produce real evidence for it to reason
   over.

---

## Proposed FIRST small implementation task

**Add the `Competitor` model + a minimal `/projects/{id}/competitors`
management UI (add/remove competitor URLs), with zero AI and zero new
provider calls.**

Why this one, first, alone:
- Every blocked feature (Backlink Gap, Keyword Gap, RivalFlow) needs this
  exact same table — building it once now prevents three separate
  competitor-list implementations later.
- It's independently testable: add a competitor, see it persist, remove
  it, see it gone. No external API involved, so nothing can be
  ambiguous about whether it "worked."
- It touches the minimum files possible: one new model, one migration,
  one small route file, one small template — no changes to any existing
  working feature (keyword research, backlinks, suggestions, scheduler
  all stay untouched).

Not proposing anything past this — per the brief's own instruction, this
stops here for review before any code is written.
