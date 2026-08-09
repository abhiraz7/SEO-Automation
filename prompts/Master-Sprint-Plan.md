# VTechSEO — Master Sprint Plan
Derived from the client scoping email (SRS input). Crawler is explicitly
out of scope here — Site Audit's data source is SEMrush's Site Audit API
+ WordPress REST API, not our own crawler/audit.py (already decided, see
`project_semrush_onpage_decision` — audit.py stays dormant, no toggle).

Status below is verified against actual code (routes, models), not
assumed from the task-list doc — some items are further along than the
client email assumes, one is further behind.

---

## Already shipped (verify + polish only, not new build)

| Tool | Real status | What's left before "done" |
|---|---|---|
| AI Fix Suggestions | Built (`app/claude.py`, `suggestions` router) | None — working end to end |
| Keyword Research | Built (Semrush + DataForSEO, location support) | None |
| Rank Tracking | Built (scheduled job, historical snapshots) | None |
| Backlink Analysis | Built, minimal version (no formal spec existed) | Referring domains/anchor text depth — check against client's exact list below |
| AI Visibility Score | **Already built** — real DataForSEO AI Overview citation + organic rank checks | Client scoped this as Phase 2; we're ahead. No action needed unless client wants the "custom scoring model" (rank+traffic+CTR+authority blend) they specifically called out — that part genuinely isn't built |
| WordPress Auto-Fix | Code + unit-test verified | **Never live-verified against a real site.** This is the one item blocking a true "done" claim — see Sprint 1 |

## Site Audit — scope correction needed

Client's email describes: *"Crawl WordPress pages (via sitemap + internal links)... via crawler + WP REST API."* That's the **old, crawler-based** approach we've moved away from. Current build uses SEMrush's Site Audit API instead (`app/semrush_audit.py`, `onpage_semrush` router) — same output (issue list + severity), different, more reliable data source, no crawler maintenance burden.

**Action: correct the client's understanding in the SRS reply** — Site Audit is MVP-complete via SEMrush, not blocked, just built differently than they described. Worth saying explicitly so it isn't mistaken for a gap.

## Not built — real gaps against the "3-Month MVP Commit" list

| Tool | Status | Sprint |
|---|---|---|
| Competitor Analysis / RivalFlow | Not started — no code anywhere | Sprint 2 |
| Content Optimization | Not started — no code anywhere | Sprint 3 |

## Phase 2 (client explicitly deferred these — no sprint assigned yet)
- Security Audit (deep vulnerability scanning) — basic checks (SSL/headers/robots.txt) already done
- RAG / Learning — Supabase tables planned, zero callers currently
- GMB Ranking

---

## Sprint 1 — Close the one real MVP gap: WordPress live-deploy proof
**Why first:** every other MVP tool is functionally done; this is the single item standing between "built" and "proven." Client's own validation flow (*"Audit → Suggestion → Approval → Auto-fix"*) is untestable without it.

- Get real WP admin/plugin credentials for `https://vseo.vtraffic.io` (client confirmed this is the staging site)
- Run the full flow once, live: Site Audit finds an issue → AI suggestion generated → human approves → deploy via WP REST API → confirm the change landed on the actual site
- Confirm rollback works against a real deployed change, not just the unit test
- **Done when:** one real fix is live on `vseo.vtraffic.io`, verified by loading the page, with a rollback proven to work

## Sprint 2 — Competitor Analysis / RivalFlow (net-new build)
Per client scope: compare page vs. competitors for the same keyword, identify content/keyword/backlink gaps, simple dashboard view.

- Data source decision: Semrush/DataForSEO competitor endpoints vs. scraping competitor pages directly (crawler-free — either an API-based content diff or a one-off fetch, not a persistent crawl)
- Gap detection: missing keywords/topics, content length delta, backlink profile delta
- Dashboard view (reuse existing project_detail.html card patterns)

## Sprint 3 — Content Optimization (net-new build)
Per client scope: AI suggestions to improve existing content, based on SERP + on-page signals, draft-level (not auto-publish).

- Pull SERP signals for the target keyword (reuse DataForSEO SERP fetch already built for rank tracking)
- Feed page content + SERP context to Claude for optimization suggestions
- Draft storage + review flow (likely extends the existing `suggestions` model rather than a new one)

## Sprint 4 — SRS reply + re-scope conversation with client
- Correct the crawler-vs-SEMrush framing on Site Audit
- Flag AI Visibility Score is already ahead of plan — ask if they still want the custom scoring model, since that's the one piece of their Phase 2 description not yet built
- Confirm Backlink Analysis' current minimal version actually covers their 4 bullet points (referring domains, count, anchor text, competitor comparison) or if it needs extending

---

## Open questions for the client (surface before Sprint 1 starts)
1. WordPress credentials/access for `vseo.vtraffic.io` — when can these be provided? This blocks Sprint 1 entirely.
2. RivalFlow: do they have a preferred data source for competitor pages, or is API-only (no scraping) an acceptable constraint?
3. AI Visibility Score's custom scoring model — still wanted, or is the current DataForSEO-based version sufficient for MVP?
