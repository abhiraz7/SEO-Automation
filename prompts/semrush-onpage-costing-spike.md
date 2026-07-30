# SEMrush Site Audit — Costing Spike

Date: 2026-07-30
Follows: `prompts/semrush-onpage-audit-plan.md` (the integration plan) and
the MVP built against it (`/onpage-semrush`, single project, hard-pinned
to `vtraffic.io`). This spike exists because the user asked, after seeing
the MVP work, whether/how to expand it to all 14 real SEMrush projects on
the account — that's a real money question, not a code question, so it's
being answered here before any code changes.

## What we know for a fact (measured this session, not estimated)

- **Per-issue-type call cost:** `snapshot/{id}/issue/{issueId}` — the
  endpoint the MVP actually uses — is documented by the plan at 100 units/
  call. Confirmed as the working, unblocked endpoint (see AgentLog
  2026-07-29).
- **Issue types covered today:** `ONPAGE_ISSUE_MAP` in
  `app/semrush_audit.py` has **10** entries (title missing/duplicate/too
  short/too long, meta description missing/duplicate, H1 missing/
  multiple, missing ALT, duplicate content). This is a deliberately
  narrow on-page subset of SEMrush's ~80+ total issue catalog.
- **Real cost per project per refresh: 10 × 100 = 1,000 units.**
  Confirmed twice — two real "Refresh from SEMrush" clicks on
  `vtraffic.io` this session, each costing exactly 1,000 units per
  `estimate_refresh_cost`.
- **The account has 14 real SEMrush Projects**, all pre-existing client
  sites unrelated to this app (redroadshop, candlewoodfencing, vtechys.com,
  vtraffic.io, etc.) — confirmed via `list_projects()`.
- **Two separate quota pools exist on this account, confirmed by direct
  contradiction:**
  1. "Standard API" balance — shown on the user's SEMrush dashboard
     (screenshot: 4,960 units), and independently confirmed live via
     `semrush.health_check()` (`countapiunits.html`, a free call) —
     observed values of 2,860 and 2,860 units at different points this
     session.
  2. A **separate Site Audit unit pool** that the full-overview endpoint
     (`snapshot?snapshot_id=`) draws from — this hit `{"status":403,
     "message":"Api units balance is zero"}` even while the Standard API
     balance showed thousands of units remaining, and even while
     `list_projects`/`list_snapshots`/the per-issue endpoint kept working.
     Likely tied to the dashboard's "Website monitoring: 15/15, 0
     available" line (matches the 14-15 project count seen).
- **Projects cap: 14/14 used.** `create_project` for a 15th project
  (`vseo.vtraffic.io`) failed with `{"code":521,"message":"Projects limit
  exceed"}`. This is a plan-tier limit, not a units problem — no amount of
  unit top-up fixes it; it needs a freed slot or a plan upgrade.

## Cost model

```
cost_per_project_per_refresh = len(ONPAGE_ISSUE_MAP) × UNITS_PER_ISSUE_CALL
                              = 10 × 100 = 1,000 units
```

| Scenario | Projects | Refreshes | Units |
|---|---|---|---|
| MVP today (as built) | 1 (vtraffic.io) | 1 | 1,000 |
| MVP today, daily refresh | 1 | 30/month | 30,000/month |
| All 14 existing SEMrush projects, one-time | 14 | 1 | 14,000 |
| All 14, daily refresh | 14 | 30/month | 420,000/month |
| All 14, weekly refresh | 14 | ~4.3/month | ~60,200/month |
| Full issue catalog (~80 types) instead of 10, all 14, one-time | 14 | 1 | ~112,000 |

The account's Standard API balance (a few thousand units, observed
2,860–4,960) is **the wrong number to size against** — it doesn't gate
this endpoint. What actually gates it is the separate Site Audit pool,
whose size/replenishment is unknown to us — we've only observed it hit
zero, not what its ceiling or refill cadence is. **This is the single
biggest unknown blocking a real cost decision**, and it isn't something
we can resolve from code; it needs a SEMrush account/billing page check
or a support ticket.

## Cost levers, in order of impact

1. **Refresh cadence is the biggest lever.** Manual, on-demand refresh
   (what's built) has zero passive cost — the account only pays when
   someone clicks the button. A scheduled auto-refresh (daily/weekly)
   turns this into a recurring bill; going from "manual" to "daily" is a
   30x cost multiplier for the same project count.
2. **Project count is the second biggest lever.** Linear: 14 projects
   costs 14x one project, per refresh.
3. **Issue-type coverage is the third lever.** `ONPAGE_ISSUE_MAP`'s 10
   types is already a deliberately narrow slice of the plan's stated
   scope (title/meta/H1/alt/duplicate content) — extending to schema,
   canonical, OG/Twitter tags, thin content thresholds, etc. (mentioned
   in the original plan but not yet mapped) would add more calls per
   project, linearly.
4. **Per-project caching already minimizes waste** — the MVP's design
   (cache-first GET, spend only on explicit refresh) already avoids the
   worst cost mistake (re-fetching on every page view). This is not a
   lever that needs further work; it's already the cheap path.

## What we don't know (needs a real answer before scaling)

- The separate Site Audit unit pool's actual size, refill cadence, and
  cost to top up (if purchasable at all — the dashboard's "API units"
  purchase flow may only apply to Standard API, not Site Audit).
- Whether "Website monitoring: 15/15" is literally the same limit as the
  Projects cap we hit (521 error), or a related-but-separate ceiling.
- Whether upgrading the SEMrush plan raises the Projects cap, the Site
  Audit unit pool, both, or neither.

## Recommendation

Do not commit to scaling past the single hard-pinned MVP project until:

1. The Site Audit-specific unit pool's real ceiling/refill is confirmed
   (SEMrush account page or support ticket — not something this codebase
   can determine).
2. A refresh cadence decision is made explicitly (manual-only, as today,
   vs. a scheduled job) — this is a product/budget decision, not a
   technical one, and it's the single largest cost multiplier available.
3. If/when scaling to more projects, do it incrementally (e.g. 2-3 more
   real projects, not all 14 at once) so real cost-per-project is
   reconfirmed against whatever the actual Site Audit quota turns out to
   be, rather than assuming today's Standard-API-balance numbers apply.

No code changes are proposed by this spike — it's a decision input, not
an implementation task.
