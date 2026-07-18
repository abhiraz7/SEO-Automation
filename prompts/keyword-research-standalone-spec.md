# Keyword Research — Standalone Tool Spec

Status: diagnosis + target design, written against commit `ded73d0`.
Goal: fix why it's silently returning empty data, and detach it from
`/projects/{id}/keywords` into its own top-level tool.

---

## 1. Why it's "not working" today (root cause, not guesswork)

Confirmed from code, not from the screenshot alone. Three independent bugs,
all of which render **identically** in the UI as a row full of `—`:

### Bug 1 — Provider errors are normalized into fake successful rows

`app/keyword_provider.py::get_keyword_overview()`:

```python
row = dataforseo.fetch_keyword_overview(keyword)
return dataforseo.normalize_keyword_row(row, keyword)   # called even if row has "error"
```

`app/dataforseo.py::normalize_keyword_row()`:

```python
if not row or row.get("error"):
    return NormalizedKeyword(keyword=keyword, source="dataforseo", fetched_at=...)
```

Every failure mode — missing credentials, network timeout, 4xx/5xx, "no data
for this keyword" — collapses into an object that is *indistinguishable from
a real answer with all-empty metrics*. The UI has no way to tell "the API
call failed" from "this keyword genuinely has no volume." This is the direct
cause of the screenshot: `dentist in new delhi` shows `—` across the board
with no error, no toast, nothing.

**Fix:** `NormalizedKeyword` (or the call site) needs a third state, not just
"has data" vs "no data" — an explicit `ok: bool` / `error: str | None`, or
`normalize_keyword_row` returns `None` on error and callers propagate that
instead of persisting a fabricated snapshot. Currently, tracking a keyword
**always** writes a `KeywordSnapshot` row even when both providers failed —
so failed lookups permanently pollute snapshot history with zero-value rows,
which will also corrupt future `compute_trend()` results (a real snapshot
diffed against a bogus zero-volume snapshot looks like a −100% crash).

### Bug 2 — Hardcoded US location on non-US queries

`semrush.py` hardcodes `database=us` in every URL. `dataforseo.py` hardcodes
`LOCATION_CODE_US = 2840` and passes it on every call. A query like "dentist
in new delhi" against the *US* keyword index will legitimately return "no
data" from a fully working, correctly authenticated API — the phrase isn't
indexed there. This is not a bug in the sense of a crash, but it silently
guarantees empty results for exactly the kind of query your Business Profile
work (Patna/Bihar/India) is aimed at.

**Fix:** location must be a parameter threaded from the business profile /
project (or, in the standalone tool, a location picker in the UI) down
through `keyword_provider.py` into both adapters. Semrush uses a country
database code (`in`, `us`, `gb`, ...); DataForSEO uses a numeric
`location_code` (city or country granularity) — these need a mapping table,
not a hardcoded constant.

### Bug 3 — No credential-state signal reaches the UI

If `SEMRUSH_API_KEY` or `DATAFORSEO_LOGIN`/`DATAFORSEO_PASSWORD` are unset,
both adapters return `{"error": "..."}` with no exception and no log visible
to the user. Combined with Bug 1, an unconfigured install renders exactly
like a configured install with no data — there's nothing telling you "you
forgot to set an API key" versus "the API has no data for this."

**Fix:** a `GET /keywords/provider-status` (or equivalent) endpoint that
reports which providers are configured, surfaced as a banner/badge in the
UI, so a blank result is legible instead of mysterious.

### Diagnostic order for you to run right now, before any code change

1. Confirm `.env` actually has non-empty `SEMRUSH_API_KEY` and
   `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD`, and that `load_dotenv()` is
   being called before these modules read `os.environ` (it's currently
   called in `app/main.py` on startup — fine, but worth confirming the
   process was restarted after any `.env` edit).
2. Add a temporary `print()`/log of the raw `row` dict in
   `keyword_provider.get_keyword_overview()` before it's normalized, and
   track one keyword. This alone will show you which of the three bugs
   you're hitting.
3. Test with a US-market keyword ("best coffee maker") against the current
   hardcoded US location — if that returns real data, Bug 2 is confirmed
   as (at least) part of the problem for Indian-market projects.

---

## 2. How Keyword Research should work (target behavior)

### 2.1 Core principle

A keyword lookup has **three possible outcomes**, and the system must be
able to show all three distinctly:

| Outcome | Meaning | UI treatment |
|---|---|---|
| `ok` | Provider returned real metrics | Show volume/difficulty/intent |
| `no_data` | Provider succeeded but has nothing for this keyword+location | "No data available" — distinct from a dash |
| `error` | Provider call failed (auth, network, rate limit, both providers down) | Visible error state, not a silent blank row |

This distinction must survive from the adapter → provider router → route →
schema → template. Right now it dies at the adapter boundary.

### 2.2 Provider abstraction (keep the existing design, fix the leak)

Keep `semrush.py` / `dataforseo.py` as adapters and `keyword_provider.py` as
the only caller of both — this part of the architecture is sound and worth
preserving as-is in the standalone tool. Change the contract:

```python
# New shape — every fetch_* function returns this instead of a bare dict
class ProviderResult:
    ok: bool
    data: dict | None       # raw provider payload, only present if ok
    error: str | None       # human-readable reason, only present if not ok
    rate_limited: bool = False
```

`normalize_keyword_row` only runs on `ok=True` results. Callers that get
`ok=False` propagate a `KeywordLookupError` (or a `None` return that the
route layer turns into a proper HTTP error / UI state) instead of writing a
fabricated snapshot.

### 2.3 Location handling

- Standalone tool UI gets a location selector (country at minimum; city
  where DataForSEO supports it) — not implicitly inherited from a project's
  business profile, since the tool no longer requires a project.
- `keyword_provider.py` functions gain a `location: str` (ISO country code
  or similar) parameter, threaded to both adapters.
- `semrush.py`: map `location` → Semrush database code (`in`, `us`, `uk`,
  `au`, ...) via a small lookup table; reject/flag unsupported codes rather
  than silently falling back to `us`.
- `dataforseo.py`: map `location` → `location_code` via DataForSEO's
  location reference (they publish a fixed list — worth caching it as a
  static JSON in the repo rather than calling their locations endpoint live
  each time).
- Default location: infer from the user's first use (e.g. India, given
  VTechys's market) but make it explicit and changeable per search, not
  buried in env vars.

### 2.4 Tabs — target behavior for each

**Overview** — lists all tracked keywords for the current user/workspace
(see §3 for what "workspace" means once this is standalone) with live
volume/difficulty/intent/trend. Trend logic (`compute_trend`, the ≥7-day-gap
and ±10% threshold rule) is correct as-is and should be kept unchanged.

**Suggestions & Questions** — seed keyword in, related + question-style
keywords out. DataForSEO-first/Semrush-fallback ordering is a reasonable
default; keep the "ASSUMPTION FLAG" comment as-is until real billing data
exists.

**Bulk Analysis** — paste up to 100 keywords, get a table back. Needs a
visible truncation notice if the user pastes more than 100 (currently
silently sliced client-side).

**Clustering** — root-token grouping is fine for MVP; no change needed
beyond fixing the underlying data (garbage in, garbage out — if all rows are
blank due to Bug 1, clustering has nothing meaningful to group).

**Saved List** — user curation, metrics frozen at save time. No changes
needed structurally.

### 2.5 What must NOT regress when detaching from projects

- `compute_trend()`'s snapshot-diffing logic
- The "honest empty state" pattern (`data_quality: "position_data_pending"`
  instead of showing `0` or a proxy number) — this same pattern should now
  also apply to the volume/difficulty fields once Bug 1 is fixed (i.e.
  `no_data` and `error` need their own honest states, not just Avg
  Position/Easy Wins)
- CSV export
- The provider fallback/cooldown behavior in `keyword_provider.py`

---

## 3. Standalone tool architecture

### 3.1 The scoping decision, reversed

Previously I recommended staying project-scoped. You've decided standalone —
that's a legitimate call, especially if the intent is "research keywords
before committing to a project" or "one keyword workspace shared across
multiple client sites," which is a real workflow gap in the current design.
Here's how to do it without creating the dangling-foreign-key problem I
flagged before.

### 3.2 Data model change

Introduce a `Workspace` (or reuse a lighter concept — call it whatever fits
VTechys's mental model, e.g. `KeywordWorkspace`) that keyword data hangs off
instead of `Project`:

```python
class KeywordWorkspace(Base):
    __tablename__ = "keyword_workspaces"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)          # e.g. "VTechys India", "Client X"
    default_location = Column(String)               # e.g. "IN"
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)  # optional link
    created_at = Column(DateTime, default=_utcnow)
```

`TrackedKeyword` and `SavedKeyword` change their FK from `project_id` to
`workspace_id`. `project_id` on `KeywordWorkspace` is **nullable and
optional** — a workspace can exist with no project at all (pure research
mode), or be linked to a project later (or from creation) if you want the
per-page `PageUnderstanding.primary_keyword` and future Rank Tracking to
connect back to a specific site.

This avoids the nullable-FK-on-every-row problem: the nullability lives on
one narrow join table (`KeywordWorkspace.project_id`), not scattered across
`TrackedKeyword`/`KeywordSnapshot`/`SavedKeyword`.

**Migration:** additive, same pattern as the Business Profile entity
migration — new table, backfill one `KeywordWorkspace` per existing project
(`name = project.name`, `project_id = project.id`), then add
`workspace_id` to `TrackedKeyword`/`SavedKeyword`, backfill from the
existing `project_id`, then drop the old `project_id` column in a later
pass once verified. Don't do the drop in the same migration as the add —
verify row counts first, same discipline as `001_business_profile_entities.py`.

### 3.3 Routes

Move from `/projects/{project_id}/keywords/*` to `/keywords/*`, workspace-scoped:

```
GET    /keywords                                    -> workspace picker / default workspace overview
GET    /keywords/{workspace_id}                     -> main page, 5 tabs
GET    /keywords/{workspace_id}/overview
POST   /keywords/{workspace_id}/track
DELETE /keywords/{workspace_id}/track/{keyword_id}
GET    /keywords/{workspace_id}/suggestions
POST   /keywords/{workspace_id}/bulk
GET    /keywords/{workspace_id}/clusters
GET    /keywords/{workspace_id}/saved
POST   /keywords/{workspace_id}/saved
DELETE /keywords/{workspace_id}/saved/{saved_id}
GET    /keywords/{workspace_id}/{keyword_id}/serp
GET    /keywords/{workspace_id}/export
GET    /keywords/provider-status                    -> new: surfaces Bug 3's state to the UI
POST   /keywords/workspaces                          -> create a new workspace
```

If a project exists and has a linked workspace, `project_detail.html` links
to `/keywords/{workspace_id}` instead of the current
`/projects/{id}/keywords`. Sidebar's "Keyword Research" link becomes
`/keywords` unconditionally (drop the `if project is defined` branch it
currently has).

### 3.4 What stays identical

`semrush.py`, `dataforseo.py`, `keyword_provider.py`'s routing/cooldown
logic, `compute_trend()`, the schemas in `schemas.py` — none of this needs
to change for the standalone move itself. The only schema change needed for
standalone-ness is the FK swap described in §3.2. Everything else in this
document (Bugs 1–3) needs fixing regardless of whether the tool is
project-scoped or standalone — don't conflate the two efforts, but do them
in the same pass since you're already in this code.

---

## 4. MVP checklist (must all pass before calling this done)

### Correctness
- [ ] Tracking a keyword with **no API keys configured** shows a visible
      "Keyword providers not configured" state — not a blank `—` row
- [ ] Tracking a keyword where **both providers return real data** shows
      real volume/difficulty/intent
- [ ] Tracking a keyword where **providers succeed but have no data** for it
      shows "No data for this keyword" — distinct from the above two states
- [ ] A failed/no-data lookup does **not** write a `KeywordSnapshot` row
      (or writes one flagged as invalid so `compute_trend` ignores it)
- [ ] Location selector actually changes results (verify with the same
      keyword against `IN` vs `US`)
- [ ] Semrush rate-limit (429) still triggers the 5-minute cooldown and
      DataForSEO fallback — write a test with a mocked 429 response
- [ ] `compute_trend()` unit tests: <2 snapshots, no snapshot ≥7 days old,
      >10% rise, >10% decline, within ±10%, position-based fallback when
      volume is missing on either side

### Standalone detachment
- [ ] `/keywords` loads with no project in context
- [ ] Creating a workspace with no linked project works end-to-end (track,
      save, cluster, export)
- [ ] Existing projects' keyword data survives the migration with matching
      row counts (verify before/after, same discipline as prior migrations)
- [ ] Sidebar link works from every page, not just `project_detail.html`

### UX
- [ ] Bulk paste >100 lines shows a truncation notice
- [ ] `alert(JSON.stringify(...))` in `krViewSerp` replaced with a real
      rendered SERP result (or explicitly deferred — but not left as a raw
      debug alert in what's meant to be a standalone product surface)
- [ ] CSV export handles non-ASCII keywords correctly (test with a Hindi
      keyword end-to-end)
- [ ] Provider-status banner visible somewhere on the page (not buried)

### Explicitly out of scope for this MVP (leave as-is)
- Avg. Position / Easy Wins ("Coming soon" until Rank Tracking, Sprint 7)
- ML-based clustering (root-token grouping is fine)
- Semrush-vs-DataForSEO priority tuning (leave the assumption flag until
  real billing data exists)

---

## 5. Suggested order of work

1. Fix Bug 1 (error propagation) — this alone will make the existing UI
   start showing something legible, even before the location fix.
2. Fix Bug 2 (location threading) — needed for any Indian-market keyword to
   ever return real data.
3. Fix Bug 3 (provider-status endpoint + banner) — cheap, makes future
   debugging visible instead of mysterious.
4. Write the `compute_trend` + provider-router unit tests (small, isolated,
   no network — good candidates before the bigger schema change).
5. Do the `KeywordWorkspace` migration and route move.
6. Re-run the full MVP checklist above against a live project.
