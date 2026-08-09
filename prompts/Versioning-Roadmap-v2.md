# VTechSEO — Versioning Roadmap v2

Supersedes `SEO AI Platform - Versioning Roadmap.md` (dated, written before
WordPress Auto-Fix / Rank Tracking / Backlink Analysis were built). Reflects
actual code state as of 2026-08-08, and the crawler-removal decision:
Site Audit runs on SEMrush's Site Audit API + WordPress REST API, not our
own crawler (`app/crawler.py` stays dormant, gated by the kill switch —
see `project_semrush_onpage_decision`). Companion doc: `Master-Sprint-Plan.md`
covers near-term sprint sequencing for what's still open; this doc is the
longer-horizon version map.

---

## V1 — Site Audit
**Status: Shipped**, via SEMrush Site Audit API (`app/semrush_audit.py`),
not the original crawler-based design. Same output contract (issue list +
severity), different data source.

## V2 — (reserved / not separately tracked)

## V3 — AI Fix Suggestions
**Status: Shipped.** Claude-generated, human-approval gated, rule-validated
before display.

## V4 — Suggestion validation & scoring
**Status: Shipped**, folded into V3's pipeline rather than a separate
version — rule validation runs inline.

## V5 — (reserved / not separately tracked)

## V6 — WordPress Auto-Fix
**Status: Built, not live-verified.** Deploy + rollback code exists and is
unit-tested. Has never been run against a real WordPress site because no
live credentials/token were available until now. **This is the top
priority in `Master-Sprint-Plan.md` Sprint 1** — client has confirmed
`https://vseo.vtraffic.io` as the staging target.

## V7 — Learning Dataset / Supabase
**Status: Not built.** Supabase client functions exist in
`app/supabase_client.py` but have zero callers anywhere in the app —
dead code, not in-progress code. Correctly deferred to Phase 2 (RAG item).

## V8 — Competitor Analysis / RivalFlow
**Status: Not built.** Zero matches in the codebase. Real gap against the
committed MVP list — see Sprint 2.

## V9 — RAG / Learning
**Status: Not built.** Depends on V7's dataset existing first (needs
historical accepted/rejected fix data to retrieve from) — correctly
sequenced as Phase 2, can't meaningfully start earlier.

## V10 — AI Visibility Score
**Status: Shipped — ahead of schedule.** Real DataForSEO AI Overview
citation checks + organic rank checks per project, live. Client's email
scoped this as Phase 2 (custom scoring model needed); the *lookup* half is
done, the *custom scoring model* (blending rank + traffic + CTR +
authority) they described is the one piece still open, if they still want
it — flagged as an open question in `Master-Sprint-Plan.md`.

## V11 — WordPress Deploy (rollback, deployment logs)
**Status: Built**, same code as V6 — the original roadmap tracked deploy
and rollback as separate versions; in practice they shipped together as
one `wordpress.py` subsystem.

---

## Items with no V-number in the original roadmap, tracked separately

| Feature | Status |
|---|---|
| Keyword Research | **Shipped.** Semrush + DataForSEO dual-provider, location support, honest error states — the "actively broken" issues noted in the prior roadmap version are fixed. |
| Rank Tracking | **Shipped.** Scheduled job populates `KeywordSnapshot.position`, which used to sit empty. |
| Backlink Analysis | **Shipped, minimal version.** No formal spec ever existed (`backlinks-tool-spec.md` is referenced by old docs but was never written) — built a working version directly instead. Depth against the client's 4 bullet points (referring domains, count, anchor text, competitor comparison) needs a checklist pass — see Sprint 4. |
| Content Optimization | **Not built.** Real gap — Sprint 3. |
| Security Audit | **Partial.** Basic checks (SSL, headers, robots.txt) shipped in `audit.py`'s rule list. Deep vulnerability scanning correctly deferred to Phase 2. |
| GMB Ranking | **Not built.** Correctly deferred — location-based tracking complexity as originally noted still holds. |

---

## What actually changed since the prior roadmap version
The prior version's core sequencing logic (fix Keyword Research before
building on it; one shared scheduler for periodic pulls; start WordPress
Auto-Fix early because it's the riskiest unknown) **turned out right** —
Keyword Research got fixed, the scheduler got built once and now serves
Rank Tracking + Backlinks + Site Audit's SEMrush refresh cycle, and
WordPress Auto-Fix is the most mature of the remaining-unverified items
(code-complete, just missing a live credential). The only real miss: WP
Auto-Fix still isn't live-verified months later — not because the code
work was wrong, but because external dependency (a real WP site + token)
sat blocked the whole time. That's now unblocked (`vseo.vtraffic.io`
confirmed available) — it's the actual next action, not further building.
