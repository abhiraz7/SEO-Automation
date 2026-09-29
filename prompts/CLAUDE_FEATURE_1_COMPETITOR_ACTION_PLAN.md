# Claude Code Task — Feature 1: AI Competitor Gap → SEO Action Plan

## Goal

Implement the **AI Competitor Gap → SEO Action Plan** feature in the existing `abhiraz7/SEO-Automation` repository.

This is NOT a request to build a new SEO platform or replace Semrush/SearchAtlas.

The user already has:
- Semrush integration
- DataForSEO integration
- existing Competitor management/overview UI
- existing provider fallback architecture
- existing FastAPI + SQLAlchemy + SQLite architecture
- existing Claude/Gemini AI provider abstraction
- existing AI suggestion approval/deploy workflow
- existing WordPress connector

Extend the existing architecture. Do not create parallel providers, parallel suggestion systems, or a second WordPress integration.

## Existing code you MUST reuse

Before changing anything, inspect these areas:
- `app/routes/competitors.py`
- `app/services/competitor_analysis.py`
- `app/templates/competitors.html`
- `app/keyword_provider.py`
- `app/dataforseo.py`
- `app/semrush.py`
- `app/models.py`
- `app/ai_provider.py`
- `app/claude.py`
- `app/prompt_builder.py`
- existing suggestion routes/models/templates
- existing migrations
- `CLAUDE.md`

Current repository already has competitor domain management and a domain-level comparison for organic keywords/backlinks. Keep that functionality working.

## Product behavior

User enters:
- target page URL
- target keyword
- location
- device

The feature should:

1. Get the current SERP using the existing provider adapter.
2. Select up to 5–7 comparable organic competitors from the SERP.
3. Fetch those competitor pages once only; NO persistent crawler.
4. Extract useful page evidence:
   - title
   - H1
   - H2/H3
   - main text
   - word count
   - basic questions/topics
5. Analyze the target page using the existing page data when available.
6. Identify only these V1 gaps:
   - topic/section coverage
   - PAA/question coverage
   - keyword/query coverage
   - search intent
   - SERP/content format
7. Generate an AI action plan.
8. Each action must be evidence-backed.
9. AI may recommend:
   - add
   - expand
   - rewrite
   - restructure
   - leave unchanged
   - create a separate page
10. If an action requires content, generate an atomic draft only after the user requests/reviews that action.
11. Never auto-publish.
12. Reuse the existing human approval + WordPress deployment system.

## Important SEO rules

Do NOT:
- create a universal SEO score
- optimize toward competitor word count
- treat keyword frequency as a ranking requirement
- claim Google requires a topic simply because competitors contain it
- copy competitor text
- recommend keyword stuffing
- automatically expand every detected gap
- mix backlink gaps into content gaps

Use wording such as:
"5/7 comparable ranking pages cover this topic."

Not:
"Google requires this topic."

Word count may be displayed as context but must NOT become an optimization target.

## Competitor selection

Start with top 10 organic results.

Classify each result:
- direct_content
- publisher
- forum
- marketplace
- government
- social
- homepage
- category
- other

Prefer comparable content pages.

Exclude/down-rank obvious:
- forums
- marketplaces
- social pages
- homepages
- category/search pages
- irrelevant formats

Do not blindly exclude large brands.

Select up to 5–7 usable competitors.

If fewer are usable, continue with the available set and show the actual count.

## Fetch strategy

Use:

1. normal HTTP fetch first
2. existing one-off extraction facilities where appropriate
3. Playwright/Crawl4AI only as fallback when the normal fetch returns a JS shell, empty content, or clear extraction failure

Do NOT build a persistent competitor crawler.

Do NOT bypass paywalls or access controls.

Every competitor fetch must have an explicit status:
- ok
- no_data
- error

A failed competitor must never appear as a successful empty page.

## Evidence model

Keep raw evidence separate from AI conclusions.

Create or extend models/migrations as necessary for:

### competitor_analysis_run
- id
- project_id
- target_url
- keyword
- location
- device
- status
- source
- created_at

### competitor_page_snapshot
- id
- analysis_run_id
- url
- position
- title
- h1
- headings_json
- text
- word_count
- fetch_method
- fetch_status
- extraction_confidence
- error
- created_at

### competitor_gap
- id
- analysis_run_id
- gap_type
- label/topic/question/query
- target_coverage
- competitor_count
- competitor_total
- evidence_json
- confidence
- recommended_action
- created_at

Do not duplicate existing models if an equivalent already exists. Extend existing structures where appropriate.

## Explicit status contract

All service/API operations must distinguish:

```json
{"status":"ok", ...}
```

```json
{"status":"no_data", ...}
```

```json
{"status":"error", ...}
```

Partial competitor fetches may use an internal partial state, but the UI must clearly show:
"5 of 7 competitors successfully analyzed."

Never render a failed provider/fetch as an empty success.

## AI action-plan output

Use structured JSON, not free-form prose.

Minimum shape:

```json
{
  "status": "ok",
  "actions": [
    {
      "id": "action_001",
      "type": "add|expand|rewrite|restructure|leave_unchanged|separate_page",
      "priority": "high|medium|low",
      "title": "...",
      "problem": "...",
      "recommendation": "...",
      "evidence": [
        {
          "type": "topic_consensus|question_consensus|query_coverage|intent|serp_format",
          "label": "...",
          "competitor_count": 5,
          "competitor_total": 7
        }
      ],
      "confidence": "high|medium|low",
      "requires_fact_check": true
    }
  ]
}
```

Claude must not invent evidence. Every recommendation must point to supplied evidence.

If evidence is insufficient, return `leave_unchanged` or omit the recommendation.

## Prompt design

Use the existing centralized prompt architecture.

Do not put large inline prompts in routes.

Give Claude:
- target URL
- keyword
- target page title/H1/headings/content summary
- SERP results
- SERP features
- intent signals
- topic consensus
- question consensus
- query coverage
- competitor format distribution

Do not dump entire competitor articles into the model when structured evidence is sufficient.

Competitor content is evidence, not source material.

## UI

Extend the existing Competitor Analysis page rather than creating a disconnected page.

Add a target-page/keyword analysis section.

Suggested layout:

```text
AI COMPETITOR GAP
Keyword: ...
Target: ...
Current position: ...

SERP
10 results
6 comparable competitors

HIGH PRIORITY

B.Ed eligibility
6/7 competitors
Your page: partial

[Review action]

Documents required
5/7 competitors
Your page: missing

[Review action]

Age limit
6/7 competitors
Your page: covered
No change recommended
```

Add:
- evidence drawer
- competitor matrix
- status messages
- refresh button
- no-data/error states

Do not introduce a giant SEO score.

## Action → existing AI suggestion workflow

When the user chooses "Draft action":
- convert the action into the existing suggestion workflow
- store it as a draft
- show before/after when applicable
- allow accept/edit/reject
- reuse existing WordPress deployment and rollback
- do not create a second suggestion status machine

## Tests

Add tests for:

1. SERP provider success
2. SERP no_data
3. SERP error/fallback
4. competitor selection
5. forum/marketplace exclusion
6. HTTP extraction success
7. extraction no_data
8. extraction error
9. partial competitor results
10. topic gap calculation
11. question gap calculation
12. keyword/query gap calculation
13. intent classification
14. structured AI response validation
15. malformed AI response
16. no-evidence recommendation rejection
17. existing suggestion approval still works
18. existing WordPress deployment still works

Run the full existing test suite before finishing.

## Guardrails

- Do not change existing Semrush/DataForSEO provider contracts unless absolutely necessary.
- Do not remove existing competitor overview functionality.
- Do not introduce Celery, Redis, Kubernetes, microservices, or a persistent crawler.
- Do not auto-publish.
- Do not silently swallow errors.
- Do not invent metrics.
- Do not create a new AI provider abstraction.
- Do not create a second WordPress connector.

## Definition of done

Feature is complete when a user can:

1. select a page + keyword
2. fetch current SERP
3. see selected comparable competitors
4. see evidence-backed content gaps
5. see an AI-prioritized action plan
6. open one action
7. generate an atomic draft
8. validate it
9. accept/edit/reject it using the existing workflow
10. optionally send the approved change through the existing WordPress connector

At the end:
- run tests
- report every changed file
- report migrations
- report assumptions/unverified behavior
- report any live API behavior that could not be tested
- do not claim completion for anything not actually verified
