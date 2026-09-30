# Audit classification + AI issue explanations: one plan, one branch

Branch: `feat/audit-classification` (all work below lands here).
Rule: one task at a time, you verify it, then commit and push. Nothing pushes to `main`
directly (a merge to `main` is what you deploy from; deploy itself is a manual dispatch).

## Why this exists

The On-Page SEO screen made normal sites look unhealthy. A missing canonical, Open Graph
or Twitter tag was counted as a defect, so 20 pages missing a Twitter card cost the site
as many points as 20 missing titles. On top of that, each finding carries one canned
sentence ("Title tag is longer than recommended.") with no numbers and no next step.

Two parts:
- **Part A** fixes what counts (classification and scoring). Mostly done.
- **Part B** makes every finding explain itself with AI. Not started.

---

## Part A: Audit classification and scoring

Design decision (made 2026-09-29): store the classification on each `Issue` row
(`impact`, `score_eligible`, `classification_version`) so an old audit keeps the meaning it
had when it ran. The rules live in `app/audit_classification.py` (`RULES`,
`CLASSIFICATION_VERSION`). Rows created before migration 028 are version 0 and still count
everything, which is why old projects look unchanged until they are re-audited.

| Task | What | Status |
|---|---|---|
| A1 | Registry, migration 028, `Issue` columns, `_issue()` stamping | Done, merged (PR #22) |
| A2 | Reclassify canonical, Open Graph, Twitter, meta-description-missing, thin-content as `score_eligible=False`; missing meta description severity error to warning; version 2 | Done, merged |
| A3 | One health function (`health_from_counts`, `project_health_score`) replacing three formulas; `page_score()` and `project_detail.html` respect `score_eligible` | Done, merged |
| A4 | Dashboard wording (below) | Next |
| A5 | Schema applicability (page-type-aware "missing schema") | Deferred, known gap |
| A6 | Tests: no canonical/OG/Twitter must not lower the score (12 tests in `tests/test_audit_classification.py`) | Done, merged |

### A4: Dashboard wording (display only, no migration, no API calls)

Findings that do not count toward Site Health stay visible everywhere, but are labelled as
opportunities instead of warnings.

- `app/routes/onpage_semrush.py`
  - Rename label "Content Quality" to "Content Signals" (line ~48).
  - Compute `opportunity_count` (issues where `score_eligible` is false). Keep
    `total_issues` as the real total. Pass the new count to the template.
- `app/templates/onpage_semrush.html`
  - Add an "Opportunities" KPI card (grid 6 to 7 columns) that filters to those findings.
  - "Critical Errors" and "Warnings" count only score-eligible issues, so they match
    what drives Site Health.
  - Site Health subtitle: scored issues vs opportunities, replacing "derived from
    DataForSEO checks".
  - Category rows (~lines 293-309): error/warning badges count score-eligible only; add
    a blue "N opportunities" badge.
- Check first: the filter buttons (`filterIssues('error' | 'warning')`) work off the
  JavaScript issue list. They must use the same eligibility split, or a click shows more
  rows than the badge says. Read that JavaScript before editing.
- Tests: counts split correctly; informational findings still render.
- Note: the "Needs Fixing / based on N issues" screen seen in a screenshot is not this
  template. It is the compact redesign target, not the live screen.

### A5: Schema applicability (deferred)

`("schema", "missing")` is `high, score_eligible=True` in the registry. A proper fix needs
a page-type signal (Article page wants Article schema, product page wants Product schema)
that the app does not have. Only the legacy `audit.py` path uses it and it is dormant
(SEMrush on-page decision), so no live score is affected today.

### Verifying Part A on real data

Old rows (version 0) all count, and the warning penalty is capped at 40, so a project with
hundreds of old warnings stays at the same score until re-audited. To see the effect:
re-audit one small project (uses DataForSEO credits) or build a tiny test project with no
OG/Twitter tags. Observed 2026-09-30 on project 9: 6 of 183 pages re-audited, 574 issues
still version 0, health 60 before and after.

---

## Part B: AI issue explanations

Each issue gets an AI-written explanation specific to the page and site:

| Field | Example |
|---|---|
| `reason` | "Your title is 78 characters. Google usually shows about 60, so the end will be cut off in search results." |
| `why_it_matters` | One or two sentences on the real effect for this page. |
| `how_to_fix` | A concrete next step, using the page's own topic and the business profile. |

### Non-negotiable rules

1. **The AI explains facts, it does not find them.** Measured values (lengths, thresholds,
   duplicate partner URL, missing-alt image list, counts) are computed in code and passed
   in. The model may not invent a number, URL or ranking claim.
2. **Detection stays deterministic.** Which issues exist, their severity, impact and
   `score_eligible` are untouched. This only adds prose.
3. **Never blank.** If generation fails or is disabled, the row shows a deterministic,
   number-filled message (B1). AI text is an upgrade, not a dependency.
4. **Explicit states:** `ok`, `no_data` (not enough evidence), `error`.
5. **Untrusted text stays untrusted:** titles, descriptions and crawled content go through
   `prompt_builder._wrap_untrusted`.
6. **Log every failure** once, at the decision point, no secrets, no draft text.
7. **No unverified claims** ("this will raise rankings by X").

### Design

- **Facts layer (code, free, always on):** per-rule function returning e.g.
  `{"length": 78, "min": 30, "max": 60, "text": "..."}`; also produces the fallback sentence.
- **Explanation layer (AI, on demand):** facts + page understanding
  (`context_builder.build_page_understanding`) + business profile, returns the three
  fields as JSON, parsed with a pydantic model.
- **When:** lazy (on category expand or row open), one call per page covering all its
  issues, cached on `(issue_id, facts_hash, prompt_version)`. Never at audit time: a
  project can hold 500+ issues.
- **Reuse:** `app/ai_provider.py` (`complete`, provider fallback), `prompt_builder`,
  `context_builder`. Overlap to resolve: `Suggestion` rows already hold replacement text,
  and the image-alt path already returns a `reason` and `confidence`.

### Tasks

| Task | What |
|---|---|
| B1 | Facts layer plus deterministic number-filled messages (no AI, no migration). Replace canned strings in `dataforseo_onpage.py:issues_from_item`. Update tests. |
| B2 | Storage (see open question 1). Migration is typed by you, not written by Claude. |
| B3 | Generator: prompt, JSON schema, batch per page, cache, fallback, failure logging, `ok/no_data/error`. Tests with a fake provider, no live API calls. |
| B4 | Route and UI hook: show `reason` on the row, `why_it_matters` and `how_to_fix` behind "Why?". Deterministic text first, AI text swaps in. |
| B5 | One live run on a small project: tone, accuracy, cost. |

### Open questions (answer before B2)

1. **Storage:** extend existing `Suggestion` (one AI call returns diagnosis and fix; only
   exists after Generate) or a new `issue_explanations` table (instant on every row, second
   AI call per issue, no ALTER on existing tables). Recommendation: extend suggestions.
2. **Duplicate partner URL:** does stored DataForSEO data say which page duplicates a
   title? Not verified. If not, say only "duplicated on another crawled page".
3. **Cost ceiling:** explanations per project per day, plus a manual "generate" fallback.
4. **Confidence:** a model's self-reported confidence is unreliable. Do not show it for
   explanations. Show it for suggestions only if backed by real data.
5. **Language:** explain in the site's language or English (some sites have Hindi titles).
6. **`failure_log`:** `services/failure_log.py` exists only on `feature/google-search-console`,
   not on `main`. Merge it first or add a minimal logger here.

### Out of scope

Changing detection or scoring; the compact UI rebuild (separate feature, will reuse these
fields); auto-applying fixes (still goes through the existing suggestion and WordPress
deploy flow).

---

## Order of work

1. A4 (dashboard wording), you check it in the browser.
2. Verify Part A on one re-audited small project.
3. Push, update `AgentLog.md` and `LEARNING_JOURNAL.md`.
4. Answer the Part B open questions, then B1 through B5, one at a time.
