# Backlinks Intelligence — Feature Spec

Roadmap: Sprint 9. Written ahead of schedule for planning purposes — actual
build should follow Sprint 3 (Crawl Engine), since backlink data needs the
same job/scheduler infrastructure crawls do (periodic re-fetch, not
one-shot).

---

## 1. What this tool needs to do

Per the roadmap: referring domains, lost links, new links, toxic links,
anchor analysis. Translated into concrete product behavior:

1. Show the current backlink profile for a project's domain: total
   backlinks, referring domains, authority score.
2. Show **change over time** — links gained and lost since the last check.
   This is the part that actually requires new infrastructure; a single
   snapshot can't show "lost" or "new" without something to diff against.
3. Flag anchor text patterns that look manipulative (heavy exact-match
   commercial anchors = an over-optimization / manual-penalty risk signal).
4. Flag backlinks that look toxic (spammy source domains, link farms).
5. (Sprint 8 territory, adjacent) — the same infrastructure will later
   support competitor backlink comparison; worth designing the data model
   so it isn't a one-off rewrite when that sprint starts.

## 2. What's confirmed available from your existing providers

**Semrush — confirmed working** (verified in your test run against
`example.com`):
- `backlinks_overview` → authority score, total backlinks, referring
  domains, referring IPs, dofollow/nofollow split. This alone covers item 1
  above, no new integration work needed for the summary numbers.
- Semrush also exposes `backlinks` (individual link list) and
  `backlinks_refdomains` — not tested yet, but same auth/key, worth a
  quick isolation test the same way you tested the phrase endpoints, since
  Semrush's per-report entitlement turned out to vary (recall the earlier
  false alarm on Keyword Analytics — don't assume access without testing).

**DataForSEO — not yet testable** (still account-gated at `40104`). Once
verified, their Backlinks API (`backlinks/summary/live`,
`backlinks/backlinks/live`, `backlinks/referring_domains/live`,
`backlinks/anchors/live`) covers the same ground plus historical/new-lost
link detection natively in some endpoints — worth checking their docs for
a "history" or "new_lost" report type once you have access, since that
could remove the need to build diffing yourself. Don't assume the shape
until you've actually seen a real response, the same lesson from Keyword
Research.

**Neither provider gives you a real "toxic score."** That's a specialized,
proprietary signal (Semrush's own Backlink Audit tool has one, but it's not
exposed the same way through the basic API you're using, and DataForSEO
doesn't publish one either as far as documented). Toxic detection here will
be a **heuristic you build**, not a number a provider hands you — see §5.

## 3. Data model

```python
class BacklinkSnapshot(Base):
    """One point-in-time pull of a domain's backlink summary."""
    __tablename__ = "backlink_snapshots"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    authority_score = Column(Integer)
    total_backlinks = Column(Integer)
    referring_domains = Column(Integer)
    referring_ips = Column(Integer)
    dofollow_count = Column(Integer)
    nofollow_count = Column(Integer)
    source = Column(String)          # "semrush" | "dataforseo"
    fetched_at = Column(DateTime, default=_utcnow)

class BacklinkRecord(Base):
    """An individual backlink, captured as part of a snapshot pull.
    This is what makes new/lost diffing possible."""
    __tablename__ = "backlink_records"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    source_url = Column(String, nullable=False)      # the page linking to you
    source_domain = Column(String, nullable=False, index=True)
    target_url = Column(String, nullable=False)       # your page being linked to
    anchor_text = Column(String)
    is_dofollow = Column(Boolean)
    domain_authority = Column(Integer)                 # authority of the linking domain
    first_seen = Column(DateTime, nullable=False)
    last_seen = Column(DateTime, nullable=False)
    status = Column(String, default="active")          # "active" | "lost"
    toxic_flag = Column(Boolean, default=False)
    toxic_reasons = Column(JSON)                        # list of triggered heuristic reasons
    source = Column(String)

    __table_args__ = (
        UniqueConstraint("project_id", "source_url", "target_url",
                          name="uq_backlink_project_source_target"),
    )
```

**Diffing logic** (runs each time a fresh pull happens):
- Any `BacklinkRecord` present in the new pull but not matched by
  `(source_url, target_url)` in the prior pull → new link, `first_seen` =
  now.
- Any record present in the prior pull but absent from the new pull →
  mark `status = "lost"`, keep the row (don't delete — you want lost-link
  history, not just a shrinking table).
- Any record present in both → update `last_seen`, keep `first_seen`
  unchanged.

This is the same pattern as `KeywordSnapshot`'s trend computation — diff
against a prior state, don't just overwrite. Reuse that discipline here.

## 4. Provider abstraction

Same shape as Keyword Research — don't reinvent this:

```
app/backlinks_provider.py   -- the only caller of both adapters
  - get_backlink_summary(domain) -> BacklinkSummaryResult (ok/no_data/error)
  - get_backlink_records(domain, limit) -> list[NormalizedBacklink]
  - get_anchor_distribution(domain) -> list[AnchorStat]
```

**Apply the three-outcome model from day one here** — `ok` / `no_data` /
`error`, exactly like the fix going into `keyword_provider.py`. Do not
repeat Bug 1 (silent error-to-blank-row normalization) in a second
provider module. This is the single most important carryover from the
Keyword Research postmortem: build the honest-failure pattern into the
adapter contract before the first line of `backlinks_provider.py` is
written, not as a retrofit later.

## 5. Toxic link heuristic (MVP version — not a provider score)

Flag a `BacklinkRecord` as `toxic_flag = True` if it matches any of:

- Source domain authority below a low threshold (e.g. <10) **and** anchor
  text is exact-match commercial (matches your target keyword list closely)
  — this combination is the classic "low quality site pointing at you with
  suspiciously optimized anchor text" pattern.
- Source domain TLD in a known-spammy set (a small maintained list —
  `.xyz`, `.top`, `.work`, certain others; treat this list as a config
  value you can tune, not a hardcoded assumption).
- A single source domain linking with the **same exact anchor text** more
  than N times (link farm / PBN pattern).
- Source domain matches known link-farm patterns if you can get a cheap
  signal for it (optional, skip for MVP if no clean data source exists).

Store `toxic_reasons` as a list so the UI can show *why* something was
flagged, not just a red badge — this matters for trust, same reasoning as
showing "Pending" instead of a fake "Stable" in Keyword Research trends.
**Be explicit in the UI that this is a heuristic estimate, not a verified
toxic score** — don't let a false "toxic" flag cause a client to disavow a
genuinely good link.

## 6. Anchor text analysis

Group `BacklinkRecord.anchor_text` by category:
- **Branded** — contains the project's brand name (from Business Profile's
  `brand` entity field — nice reuse of the entity model work from Sprint 1)
- **Exact-match commercial** — matches a tracked keyword closely
- **Generic** — "click here," "read more," "website," etc.
- **Naked URL** — anchor text is the URL itself
- **Other**

Show as a distribution (bar chart or percentage breakdown). A healthy
profile is branded/generic-heavy; a spike in exact-match commercial anchors
is a real over-optimization signal worth surfacing, unlike the toxic-link
heuristic which is inherently fuzzier.

## 7. Routes

```
GET  /projects/{id}/backlinks                     -> page, tabs
GET  /projects/{id}/backlinks/overview             -> summary stats + trend
POST /projects/{id}/backlinks/refresh               -> trigger a new pull
GET  /projects/{id}/backlinks/new                   -> links gained since last snapshot
GET  /projects/{id}/backlinks/lost                  -> links lost since last snapshot
GET  /projects/{id}/backlinks/toxic                 -> flagged records + reasons
GET  /projects/{id}/backlinks/anchors               -> anchor distribution
GET  /projects/{id}/backlinks/export                -> CSV
```

Kept project-scoped (unlike Keyword Research) — backlinks are inherently
about *your* domain's authority, there's no clean "standalone research"
use case the way keyword research has. If a competitor-comparison feature
lands in Sprint 8, that's a separate `CompetitorBacklinkSnapshot` reusing
the same `BacklinkRecord` shape, not a reason to make this one standalone.

## 8. UI — tabs

- **Overview** — authority score, total backlinks, referring domains,
  trend arrows (reuse the same up/down/pending pattern as Keyword
  Research's trend column)
- **New Links** — table of recently gained backlinks
- **Lost Links** — table of recently lost backlinks (this is the tab most
  agencies check first — losing a high-authority link is often more
  urgent than gaining one)
- **Toxic** — flagged links with reasons, and a manual "dismiss flag"
  action for false positives (heuristics will have false positives —
  design for that from the start, don't treat the flag as final)
- **Anchor Analysis** — distribution chart

## 9. Scheduling

Backlink data is slow-moving compared to keyword volume — weekly or
bi-weekly pulls are enough, no need for the daily cadence keyword tracking
might eventually want. This fits naturally into Sprint 3's scheduler once
it exists (same job-scheduling infrastructure, different job type) rather
than needing its own scheduling mechanism built from scratch. This is the
core reason to sequence this after Sprint 3, not before.

## 10. MVP checklist

- [ ] `backlinks_provider.py` built with the three-outcome (`ok`/`no_data`/
      `error`) contract from day one — no silent-failure regression
- [ ] Semrush `backlinks_overview` wired to Overview tab, verified against
      a real project domain (not `example.com`)
- [ ] `backlinks`/`backlinks_refdomains` Semrush endpoints isolation-tested
      before assuming they're accessible on this key/plan
- [ ] First snapshot pull populates `BacklinkRecord` rows correctly
- [ ] Second pull (manually triggered a day later, or against slightly
      different data for testing) correctly identifies new vs. lost links
- [ ] Toxic heuristic flags at least the obvious cases (test with a
      deliberately spammy-looking mock record) without flagging normal
      high-authority links
- [ ] Anchor distribution correctly buckets branded vs. commercial vs.
      generic using the Business Profile's `brand` field
- [ ] CSV export works
- [ ] Empty state (brand-new project, zero backlinks yet) renders honestly,
      not as an error

## 11. Explicitly out of scope for MVP

- DataForSEO integration (blocked on account verification anyway — build
  Semrush-only first, add DataForSEO as a second source once unblocked,
  same pattern as Keyword Research)
- Real toxic scoring via a paid dedicated API (e.g. Semrush's Backlink
  Audit as a distinct product) — the heuristic is enough for MVP
- Competitor backlink comparison (Sprint 8's job)
- Disavow file generation — useful eventually, not MVP
