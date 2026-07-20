# Changelog

Dated, evidence-backed entries — pulled from real audits run against the
live codebase and database, not a generic commit-message summary. See
`prompts/audit-log-compiled.md` for the full methodology and findings
behind each entry.

## 2026-07-20

### Fixed
- **Job queue starvation.** A single long-running `crawl` job could occupy
  the app's one worker slot for 5+ minutes, blocking `rank_check`,
  `backlink_pull`, and `keyword_refresh` jobs queued behind it — even when
  those jobs were already due. Split the worker into two independent
  lanes: crawl (network/browser-bound, 900s timeout) and light jobs
  (rank_check/keyword_refresh/backlink_pull/audit, new 180s timeout so a
  hung API call can't reintroduce the same problem from the other
  direction). Verified live: a rank_check job completed in 9 seconds while
  a crawl job queued just before it was still running minutes later.
- **Backlinks and Rank Tracking were fully built but undiscoverable.**
  Both features (models, background jobs, routes, working UI panels) were
  complete and working, but their sidebar links were hardcoded disabled
  (`href="#"`, greyed out, "coming soon" tooltip) — dead links pointing at
  real, working features. Backlinks now links to its panel on the project
  page; the standalone "Rank Tracking" nav item was removed (it isn't a
  separate page — it's a schedule toggle inside Keyword Research) and
  replaced with a small "includes Rank Tracking" note under the Keyword
  Research nav item.

### Verified (no code change needed)
- Confirmed the background scheduler's automatic tick loop fires without
  manual intervention: `dispatch_due_schedules` (60s interval) creates due
  jobs from `Schedule` rows, and the worker tick picks them up and runs
  them end to end — this was the one previously-unverified link in the
  automation chain.
- Confirmed 3 of 4 WordPress-plugin security-introspection signals (list
  users/roles, read WP-Cron entries, list installed plugins including
  deactivated ones) are achievable today with the existing plugin tool
  set; file-level backdoor detection is not (no file-read/list tool
  exists in the plugin).

### Known issues
- `KeywordSnapshot.position` is landing `null` on live rank-tracking runs.
  Root cause identified: the configured DataForSEO account is unverified
  and returns `403 Forbidden` on every call, silently degrading every
  SERP check to Semrush's fallback (10 results instead of ~100). See
  `docs/dataforseo-account-blocker.md` for the full writeup and the
  action needed to resolve it.
- Backlinks has no automated test coverage yet.
- No "toxic backlink" heuristic exists yet (flagged as a gap, not started).

## Earlier development cycle

The initial build (crawl engine, audit engine, AI suggestions, WordPress
deploy + rollback, keyword research, backlinks, rank tracking, the
job/schedule system) was delivered prior to this changelog's start date.
See `prompts/audit-log-compiled.md` (Audits 1–8) for the dated, evidence-
backed record of what was verified built, partially built, or not started
at each checkpoint along the way, and `AgentDailyLog/AgentLog.md` for the
day-by-day session log.
