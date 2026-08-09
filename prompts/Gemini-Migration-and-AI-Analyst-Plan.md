# Gemini Migration + AI SEO Analyst Layer — Plan

Two decisions bundled together, sequenced so each is independently shippable
and testable (per MasterPlan Rule 12 — finish one before the next):
1. Replace Claude with Gemini as the app's LLM provider, everywhere.
2. Build the "Collect → Analyze → Find Opportunities → Prioritize →
   Explain → Recommend" synthesis layer, full 9-item scope, on Gemini.

---

## Phase 1 — Provider swap (Claude → Gemini), zero new features

**Why first:** every later phase needs a working LLM call. Swapping the
existing 3 call sites first means the new AI Analyst layer is built on the
final provider from day one, not built on Claude then re-migrated.

### 1.1 Fix the secret name
GitHub secret is currently `GEMENI_KEY` (typo). Rename to `GEMINI_API_KEY`
in GitHub Secrets (delete + recreate, same value) before any code
references it — cheaper to fix now than after it's wired into the deploy
workflow and multiple files.

### 1.2 New module: `app/gemini.py`
Mirrors `app/claude.py`'s exact public interface so callers change their
import line only, not their calling code:
- `complete(prompt, max_tokens, temperature=1.0, model=...) -> str`
- `generate_suggestions(context: dict) -> list[str]`
- `generate_meta_optimization(context: dict) -> dict`

Internals swap `anthropic.Anthropic` for Google's Gemini SDK
(`google-genai`), reading `GEMINI_API_KEY` instead of `ANTHROPIC_API_KEY`.
`prompt_builder.py`'s prompt construction is reused as-is — prompts don't
need to change for a provider swap, only the client call.

### 1.3 Migrate call sites
- `app/routes/keywords.py`
- `app/routes/suggestions.py`
- `app/services/context_builder.py`

Each currently does `from .. import claude` / `claude.generate_suggestions(...)`
etc. — swap the import to `gemini`, confirm output shape is unchanged
(both return the same `list[str]` / `dict` shapes per the interface above).

### 1.4 Retire `app/claude.py` + `ANTHROPIC_API_KEY`
Once all 3 call sites are confirmed working on Gemini, delete
`app/claude.py`, remove `ANTHROPIC_API_KEY` from `.env.example`, GitHub
Secrets, and the EC2 box's `.env`. Don't leave a dead second provider
around "just in case" — one provider, one code path.

### 1.5 Verify
Run the existing AI Fix Suggestions flow end-to-end (an issue → Gemini
suggestions → accept → deploy) locally, confirm output quality is
comparable to Claude's before calling this phase done.

**Done when:** zero references to `anthropic`/Claude remain in `app/`,
all 3 features (suggestions, meta optimization, keyword-related AI calls)
work identically on Gemini, verified live not just unit-tested.

---

## Phase 2 — AI Analyst layer, scoped by what already has data

Ordered by dependency — items 1-2 need nothing new built first; items 3-5
depend on features from `Master-Sprint-Plan.md` that don't exist yet.

### 2.1 Keyword research & opportunity discovery — buildable now
Data exists: `KeywordSnapshot` history. New: a Gemini synthesis pass that
reads recent snapshots for a project and outputs prioritized opportunities
("these 5 keywords have rising volume + your content doesn't rank for
them yet") rather than just listing raw numbers.

### 2.2 Backlink profile analysis — buildable now
Data exists: `BacklinkSnapshot` + `BacklinkRecord` (new/lost/active).
Synthesis pass explains *why* a change in referring domains matters, not
just that it happened.

### 2.3 Backlink competitor link-gap analysis — **blocked**
Depends on `Backlink-Analysis-Audit-and-Plan.md`'s competitor comparison
feature, which isn't built yet (needs the shared `project_competitors`
storage decision resolved first, per that doc's open question 1).

### 2.4 SERP and competitor analysis — **blocked**
Depends on RivalFlow (Sprint 2, `Master-Sprint-Plan.md`) — zero code
exists today.

### 2.5 Content gaps and search-intent analysis — **blocked**
Depends on Content Optimization (Sprint 3) — zero code exists today.

### 2.6 Technical/on-page SEO audits — mostly buildable now
Data exists: `Issue`, `SemrushOnPageSnapshot`. Synthesis pass prioritizes
by real impact rather than just listing every issue equally.

### 2.7 SEO scoring & prioritization — buildable now, once 2.1/2.2/2.6 exist
A cross-tool synthesis step that reads outputs from the above and ranks
"what to fix first," which only makes sense once there's more than one
signal source feeding it.

### 2.8 Meta titles/descriptions/headings/schema suggestions
Already exists (`generate_meta_optimization`, `generate_suggestions`) —
Phase 1 already migrates this to Gemini. No new build here, just a
provider swap already covered above.

### 2.9 Ongoing monitoring of new/lost rankings, backlinks, opportunities
Needs the existing scheduler (`app/scheduler.py`, already running
`rank_check`/`keyword_refresh`/`backlink_pull` jobs) to also trigger a
periodic Gemini synthesis pass, writing results somewhere durable (new
model, e.g. `AnalystInsight`) rather than only computing on page-load.

**Real sequencing implication:** 3 of the 9 items (2.3, 2.4, 2.5) are
blocked on features that don't exist. Building "the full 9-item list" as
one phase isn't literally possible yet — those three become their own
sub-phase once their dependencies (backlink-gap, RivalFlow, Content
Optimization) are built.

---

## Suggested order
1. **Phase 1** (provider swap) — start here, it's fully unblocked and
   everything else depends on it.
2. **Phase 2, items 2.1/2.2/2.6/2.7** — buildable immediately after
   Phase 1, no other feature dependencies.
3. **Phase 2, item 2.9** (monitoring) — once 2.1/2.2/2.6 exist to monitor.
4. **Phase 2, items 2.3/2.4/2.5** — each unblocks only once its
   prerequisite feature (backlink-gap, RivalFlow, Content Optimization,
   respectively) is built.

## Open questions
1. Confirm `google-genai` (Google's official Python SDK) is the intended
   package, and which Gemini model (e.g. `gemini-2.5-flash` vs `-pro`) —
   affects both quality and per-call cost.
2. Cost check needed before Phase 2.9 (scheduled synthesis) goes live —
   same discipline as the SEMrush costing spike: measure real per-call
   cost before committing to a recurring schedule across all projects.
