# Project Audit & Handover — 2026-09-23

Purpose: reconcile the original vision (MasterPlan) and every subsequent plan/roadmap doc
against what's actually built in code today, so anyone picking this project up — human or
agent — knows what's done, what's deliberately deferred, and what's a genuine open gap.

Method: read every doc in `prompts/`, read `AgentDailyLog/AgentLog.md` in full, cross-checked
against `app/models.py`, `app/routes/*.py`, `app/jobs/registry.py`, `migrations/*.py`, and
targeted greps for TODO/stub/dormant markers. Where something couldn't be confirmed by direct
code read (rather than doc claims), it's marked **unconfirmed** below — verify before treating
as fact.

---

## 1. Which doc is "current truth"

Docs were written over ~3 months and several supersede each other. Chain, oldest → newest:

1. `VTechSEO - MasterPlan.md` — original vision, Phases 1–11, no status markers. Never updated.
   Still useful for the *why* (logic-first, no microservices, one feature at a time) but not
   for status.
2. `SEO AI Platform - Versioning Roadmap.md` (V1–V11) — superseded by:
3. `Versioning-Roadmap-v2.md` — self-declares it supersedes the above, reflects code state as
   of 2026-08-08.
4. `vtechys-claude-code-task-list.md` — a 24-task execution list, own tracker at the bottom,
   last touched 2026-07-19 ("20 of 24 tasks fully complete"). Narrower scope (Phases 1–6 only).
5. `Master-Sprint-Plan.md` — tighter, client-facing reconciliation, checked against actual code.
6. `VTechSEO-Architecture-Audit.md` — deep-dive on competitor/provider architecture, tied to the
   2026-08-09 session.
7. **`Senior-Engineer-Evaluation-2026-09-04.md` — most current comprehensive status doc.**
   Synthesizes everything above plus three parallel reviews (architecture/security/SEO-logic).
   Treat this as the baseline; everything after it (09-17, 09-18, 09-20 work, this doc) is
   incremental on top of it, not a re-basis.

**Superseded / historical only:** the original (non-v2) roadmap, and the feature specs
(`keyword-research-standalone-spec.md`, `Backlink-Analysis-Audit-and-Plan.md`,
`semrush-onpage-audit-plan.md`, `backlinks-tool-spec.md`) — their work is complete, they're
design-rationale artifacts now, not status trackers.

**`UIImplementation.md`** targets a Next.js/Shadcn/React stack that was never built — actual UI
is FastAPI/Jinja/HTMX. Treat as an unexecuted exploration, not a plan we're behind on.

---

## 2. Feature-by-feature: planned vs. actual

| Feature area | Actual state | Verdict |
|---|---|---|
| Website Crawl | `app/crawler.py` built, but **deliberately disabled** — `routes/crawl.py` returns 403 via `CrawlerSettings` kill switch | Deferred, deliberate |
| On-Page Audit | Legacy `app/audit.py` dormant; live path is `semrush_audit.py` + `dataforseo_onpage.py`, provider-switchable | Done (different shape than planned) |
| AI Fix Suggestions | `app/claude.py` + `app/gemini.py` via `ai_provider.py` dispatcher. Generates 3 suggestions, plan said 5 | Done (minor unmet acceptance criterion) |
| Rule Validation | Folded into the suggestion pipeline, no standalone validator | Done, different shape |
| LLM Judge (Phase 5) | Zero code | **Not started — deliberately deferred**, not in current 3-month scope |
| Acceptance Tracking | `Suggestion.status` (pending/accepted/rejected/edited/deployed) built | Done locally; Supabase sync not built |
| Learning Dataset / Supabase | `app/supabase_client.py` exists, **zero callers anywhere** | Not started, correctly blocked on Phase 5/6 data |
| Competitor Analysis (RivalFlow) | CRUD + basic overview built (`Competitor`, `CompetitorSnapshot`, `routes/competitors.py`) | **Partial** — Keyword Gap / Backlink Gap / content-gap analysis, the actual point of RivalFlow, not started. Stopped mid-build 2026-08-09 *by your own instruction*, not abandoned |
| RAG (Phase 9) | Zero code | Not started, correctly blocked on Phase 7 |
| AI Visibility | `routes/visibility.py`, `VisibilityCheck` model — live DataForSEO AI Overview + organic rank checks, shipped ahead of schedule | Done for lookup; custom scoring model (rank+traffic+CTR+authority blend) still open — flagged as a **question for the client**, not a technical blocker |
| WordPress Deploy | Full deploy/rollback pipeline, Fernet-encrypted tokens, new `vtechseo-agent` plugin (23 tools, rebranded 2026-09-20) | Done (code) — **live-verification status is ambiguous, see §4.1 below** |
| Keyword Research | Fully built (`keyword_provider.py`, `semrush.py`, `dataforseo.py`, workspace model, scoring, content briefs) | Done |
| Rank Tracking | `jobs/handlers/rank_check.py`, wired into registry | Done |
| Backlink Analysis | `BacklinkSnapshot`/`BacklinkRecord`, dual-provider pull job | Partial — toxic-link heuristic from the spec **unconfirmed** in `BacklinkRecord`'s columns, needs a direct read before calling it done or missing |
| Content Optimization (SERP-informed) | Zero code | Not started |
| Security Audit | Basic SSL/headers/robots.txt checks live, independent of the crawler kill switch | Partial — deep vulnerability scanning deliberately deferred |
| GMB Ranking | Zero code | Not started, deliberately deferred |
| Scheduling/Jobs | 6 job types wired, two-lane APScheduler, WAL+busy_timeout, orphan recovery | Done |
| Indexation checking (this week's discussion) | No dedicated route/model in code | Not started — new idea, not in any prior plan doc |
| Deploy/Infra | **Docker + EC2 + GitHub Actions/SSM**, not the task-list's NSSM/Caddy plan | Done — deliberate deviation (see your own `project_deploy_stack_decision` memory), not a gap |
| Image Alt Text | AI suggestion pipeline shipped 2026-09-20 (`3bacd8f`) | Partial — `image_alt` still **not** in `DEPLOYABLE_CATEGORIES` (`routes/onpage_semrush.py`), so there's no deploy-to-WordPress button for it yet. Deliberately deferred per 2026-09-18 log |
| **Authentication** | **Zero auth anywhere** — no login/session/current_user pattern in `app/main.py` or any route | **Not started, not deferred by any doc — called out as Critical in the Sept 4 evaluation** |

---

## 3. Notable doc-vs-code corrections

- **crawler.py vs audit.py, conflated in one doc.** The Sept 4 evaluation says "both on-page
  systems are live, not one dormant" — but that's describing `crawler.py` (still runs for
  content-extraction / WP post-ID resolution) getting confused with `audit.py` (genuinely
  dormant, not called from the live job chain). For this handover: `audit.py` is dormant,
  `crawler.py` is only crawl/audit-disabled, not fully dead.
- **`backlinks-tool-spec.md`** — `Versioning-Roadmap-v2.md` claims this spec "was never
  written," but it exists in the repo today with a full design. Likely written *after* the
  backlinks feature shipped, documenting after the fact rather than planning ahead of it. Don't
  trust v2's claim literally if you read it later.
- **Suggestion count** — MasterPlan's Phase 3 "done when" criterion says 5 suggestions; code
  generates 3. Flagged as trivial/low-risk across three docs, never fixed. Still technically an
  unmet original acceptance criterion if anyone audits against the MasterPlan literally.

---

## 4. Open items that need a decision or answer, not just more coding

1. **WordPress live-deploy status is genuinely ambiguous.** `Master-Sprint-Plan.md` and
   `Versioning-Roadmap-v2.md` both say the deploy pipeline is code-complete but never
   live-verified (blocked on live site credentials). AgentLog's 2026-09-17/09-18 sessions
   describe active connection testing against real sites (`vseo.vtraffic.io`,
   `examnotespdf.in`) and fixing a stall bug in that flow — implying *some* live connection
   exists — but no entry states plainly "a real fix was deployed to a live page and rolled
   back," which was Sprint 1's explicit "done when" bar. **I don't have an answer to this from
   the docs — do you know if that's actually happened yet?**c
2. **AgentLog.md is ~5 days stale.** Last dated entry is 2026-09-18; commits exist through
   2026-09-20 (`3bacd8f` alt-text feature, `c9b81c0` PR #1 merge from a collaborator
   `bugignore`) with no prose summary. This is a gap against your own Rule 13. Worth appending
   a 09-20 entry before this staleness compounds further.
3. **SEMrush account constraints unresolved** — Projects cap 14/14, API units at zero (per your
   existing `project_semrush_api_constraints` memory). No later log entry shows this resolved.
4. **DataForSEO account verification** — flagged since 2026-07-18 as unverified/external,
   blocking a second provider path for rank tracking. No later confirmation found.
5. **No authentication, no SSRF protection** — the two Critical findings from the Sept 4
   review. Genuinely unstarted, blocks any real production/public exposure. Not deferred by any
   plan doc — this is a real gap, not a scheduled-later item.
6. **Token-leak question from 2026-09-18** left explicitly open pending you supplying a
   Network-tab response body — unresolved as of the last log entry.
7. **A collaborator (`bugignore`) merged PR #1** on 2026-09-20 ("fix/test-syntax-and-regression-check"),
   bringing in daily logs, learning journal entries, and a `Task_Bug_Log.xlsx`. Worth confirming
   you're aware of/reviewed that merge, since it wasn't narrated in AgentLog.

---

## 5. What "done" looks like right now, in one paragraph

Core product loop (crawl-adjacent on-page audit via SEMrush/DataForSEO → AI suggestions →
accept/edit/deploy to WordPress → visibility checks → keyword research → rank tracking →
backlink pulls → basic competitor overview → basic security checks) is built and wired through
the job/scheduler registry, deployed on Docker/EC2 with GitHub Actions. What's *not* built are
the "intelligence layer" features that depend on accumulated data (LLM Judge, Supabase learning
dataset, RAG) — correctly sequenced as blocked, not abandoned — plus the RivalFlow gap-analysis
features (paused by your own call, not incomplete-by-accident), and two hard production
blockers: auth and SSRF protection.

---

*One honest limitation of this audit: not every model class and route file was read line-by-line
(e.g. `BacklinkRecord`'s toxic-flag columns) — verified via targeted greps and AgentLog's own
narrative instead. Anything marked unconfirmed above needs one direct read before being stated
as fact in a client-facing document.*
