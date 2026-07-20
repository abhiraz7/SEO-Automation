# DataForSEO Account Blocker — Audit &amp; Action Request

**Date:** 2026-07-20
**Prepared for:** Client / account owner
**Prepared by:** VTechys SEO Automation team
**Severity:** High — actively degrading two live product features
**Ask:** Verify the DataForSEO account within **3 business days** (by 2026-07-23), or approve one of the alternatives in Section 5.

---

## 1. The problem, in one sentence

Every call our app makes to DataForSEO — the primary data provider for Rank Tracking and part of Keyword Research — is being rejected with `403 Forbidden` because the DataForSEO account has never completed identity verification, and every one of those calls is silently falling back to a weaker backup provider (Semrush) instead of failing loudly.

## 2. Evidence

Live probe against the account's own credentials, run today:

```
DataForSEO health_check() ->
{
  "configured": true,
  "ok": false,
  "detail": "Please verify your account before using the API.
              You can complete verification in the user panel:
              https://app.dataforseo.com/ ."
}
```

Direct SERP call, same result:

```
dataforseo.fetch_serp("dentist near me", "IN") ->
403 Forbidden — https://api.dataforseo.com/v3/serp/google/organic/live/advanced
```

This is **not** a credentials typo, an expired key, or a code bug — the credentials are present and correctly read from environment config (`DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD`), and the request reaches DataForSEO's servers successfully. The account itself is the block: DataForSEO requires a one-time identity/email verification step in their dashboard before the API will serve *any* account, regardless of plan or balance.

## 3. What this is costing us right now

Our app was deliberately built with a fallback chain (DataForSEO → Semrush) so a dead provider degrades service instead of breaking it outright — that safety net is working as designed. But "degraded" is not "fine," and it's silent, so nobody sees it happening:

| Feature | Normal (DataForSEO) | Current (forced onto Semrush fallback) | Impact |
|---|---|---|---|
| **Rank Tracking** (`rank_check` job) | Checks up to **~100** organic results per keyword for the project's own domain | Checks only the **top 10** results | Any keyword where the site ranks #11–100 reports as "not ranking" (position = null) — false negatives on real rankings, not just missing data |
| **Keyword Research → Related Keywords** | DataForSEO's Labs API, broader semantic coverage | Falls back to Semrush's related-keyword pool | Narrower, less relevant keyword suggestions |
| **Keyword Research → Questions** | Derived from DataForSEO's related-keyword set | Falls back to Semrush equivalent | Same narrowing as above |
| **Keyword Overview / Bulk Analysis** | Semrush is already primary here, DataForSEO is only the secondary fallback | Unaffected | No impact — this path doesn't depend on DataForSEO being healthy |

**Bottom line:** Rank Tracking — the feature we just finished building and verifying end-to-end (scheduler, job queue, snapshot writes, Easy Wins card) — is technically "working" but reporting **false "not ranking" results** for anything outside the top 10, because it's silently running on a 10-result fallback instead of the ~100-result depth it was designed for. This isn't a hypothetical: we confirmed it live today against the `vseo.vtraffic.io` project's own tracked keywords.

## 4. Why this needs a deadline, not just a note

- Every rank-tracking snapshot written **while the account is unverified** is a data point we may need to explain or discard later — it understates real rankings, and there's no flag in the UI today distinguishing "genuinely not ranking" from "only checked the top 10 because the primary provider was down." Left unresolved, this quietly erodes trust in the rank data the client will eventually use to judge SEO progress.
- The fallback is invisible by design (that was the right call for uptime) — which means **nobody will notice this on their own**. It only surfaces via an audit like this one.
- Verification is a five-minute action on DataForSEO's end (email/ID check in their dashboard) — this is not a technical fix on our side, it's purely an account-ownership action.

## 5. What we need from the client, by **2026-07-23**

Pick one:

1. **(Preferred) Complete DataForSEO account verification.** Log in at [app.dataforseo.com](https://app.dataforseo.com/), complete the verification flow referenced in the error above, confirm balance/plan is active. We'll re-run the health check same-day and confirm full-depth rank tracking is restored.
2. **Provide a different, already-verified DataForSEO account's credentials**, if one exists elsewhere in the org.
3. **Explicitly accept the Semrush-only fallback as the permanent depth** for Rank Tracking and Keyword Research, if DataForSEO isn't going to be pursued — we'll then add a visible "limited depth" indicator to the UI so the 10-result cap is disclosed to whoever reads the rank data, instead of silent.

Whichever option, we need an answer by the deadline above so we can either confirm the fix or ship the disclosure UI (item 3) as the accepted permanent state — leaving it silently degraded is the one outcome we want to rule out.

## 6. Once resolved

We'll re-run the same health check and a live rank_check job against `vseo.vtraffic.io`, confirm `_source: "dataforseo"` on the resulting snapshots (vs. today's `"semrush"`), and report back same-day.
