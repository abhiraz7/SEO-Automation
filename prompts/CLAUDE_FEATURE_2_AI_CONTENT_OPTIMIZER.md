# Claude Code Task — Feature 2: AI Content Optimizer → Validated Draft

## Goal

Implement the **AI Content Optimizer** in the existing `abhiraz7/SEO-Automation` repository.

The user already knows Semrush/SearchAtlas-style SEO tools.

This feature must therefore NOT be a generic "AI writer".

Its job is:

```text
existing page
→ SERP evidence
→ identify a specific opportunity
→ generate atomic edit
→ validate edit
→ human approval
→ existing WordPress deploy
```

Everything AI-generated is a DRAFT.

Never auto-publish.

## Existing architecture you MUST reuse

Before coding, inspect:
- `app/routes/suggestions.py`
- `app/models.py`
- `app/ai_provider.py`
- `app/claude.py`
- `app/gemini.py`
- `app/prompt_builder.py`
- `app/services/context_builder.py`
- `app/routes/wordpress.py`
- existing suggestion templates
- existing migrations
- `app/keyword_provider.py`
- `app/dataforseo.py`
- `app/semrush.py`
- `CLAUDE.md`

The repository already has:
- Claude/Gemini provider abstraction
- centralized prompt builder
- AI suggestion model
- accept/reject/edit/deployed state machine
- duplicate suggestion protection
- WordPress deployment + rollback

Extend those systems. Do not build a second AI suggestion workflow.

## User flow

User selects:
- existing page
- target keyword

The tool loads:
- existing page content
- title
- meta description
- H1/H2/H3
- internal links where available
- target keyword
- SERP data
- PAA/questions
- related searches
- top-result headings/sections
- SERP format
- search intent
- competitor gap evidence when available

Then it produces a small number of high-value atomic recommendations.

## V1 optimization opportunities

Only support:

1. add missing section
2. expand weak section
3. rewrite unclear section
4. improve heading
5. improve title
6. improve meta description
7. add useful FAQ/question answer
8. improve internal-link opportunity

Do NOT generate a complete article rewrite.

Maximum initial suggestions: 5.

Prefer fewer suggestions when evidence is weak.

The system must be allowed to say:

```text
No change recommended.
```

## SERP signal priority

Use this order:

1. search intent
2. SERP/content format
3. recurring competitor sections/topics
4. PAA/questions
5. related searches
6. relevant query/keyword coverage

Do not treat word count as an optimization target.

Do not use keyword density as a target.

## AI prompt contract

Use the existing centralized `prompt_builder.py`.

Do not put the complete prompt in a route.

Claude should receive structured evidence.

System instructions must explicitly state:

- do not invent facts
- do not invent statistics
- do not invent dates
- do not invent citations
- do not copy competitor wording
- do not add content merely to increase word count
- do not repeat the target keyword unnaturally
- do not change factual meaning without evidence
- prefer the smallest useful edit
- every recommendation must cite supplied evidence
- unsupported factual claims must be flagged
- output is a draft for human review

The model must return JSON only.

## Required suggestion schema

Use a Pydantic model for validation.

Example:

```json
{
  "status": "ok",
  "suggestions": [
    {
      "id": "sug_001",
      "type": "add_section",
      "section_id": "sec_04",
      "priority": "high",
      "problem": "The page does not answer the recurring question about B.Ed eligibility.",
      "evidence": [
        {
          "type": "topic_consensus",
          "label": "B.Ed eligibility",
          "competitor_count": 6,
          "competitor_total": 7
        }
      ],
      "before": "...",
      "after": "...",
      "requires_fact_check": true,
      "claims_to_verify": [
        "..."
      ],
      "confidence": "high"
    }
  ]
}
```

For title/meta changes, `before` and `after` should contain the actual current/proposed values.

For new sections, `before` may be null.

## Evidence requirement

Every suggestion must contain evidence.

Examples:

```text
5/7 comparable competitors cover this topic.
```

```text
PAA contains this question.
```

```text
Current page does not contain an equivalent section.
```

Do not allow Claude to manufacture competitor counts.

The application should build evidence and pass it to Claude.

Claude only interprets evidence.

## Validation pipeline

Every generated suggestion must pass deterministic validation BEFORE being shown as ready for approval.

Implement:

### 1. Schema validation
- valid JSON
- required fields
- valid enum values
- non-empty proposed content

### 2. Structural validation
Check:
- heading validity
- H1/H2/H3 structure
- title/meta length
- HTML validity where applicable

Do not use arbitrary SEO rules as absolute ranking rules.

### 3. Keyword repetition check

Calculate before/after:
- exact target keyword occurrences
- normalized keyword occurrences
- meaningful repeated n-grams

Flag unusually large increases.

Do not enforce a universal keyword-density percentage.

### 4. Duplication/similarity check

Compare proposed text against:
- existing page content
- other known site content where available

Use the project's existing similarity infrastructure if present.

If none exists, implement a small, isolated similarity helper rather than a new service.

Do not call semantic similarity "plagiarism".

It is only a duplication-risk signal.

### 5. Factual-claim extraction

Every suggestion containing factual statements should expose:

```json
{
  "requires_fact_check": true,
  "claims_to_verify": ["..."]
}
```

Do not silently mark unsupported factual claims as verified.

For high-risk factual content, validation status should be:

```text
needs_human_verification
```

### 6. Brand/tone validation

Reuse the existing BusinessProfile.

Validate:
- language
- tone
- audience
- forbidden phrases if configured

Use deterministic checks first.

An LLM classifier may be used only as a secondary signal.

## Validation result contract

Use explicit:

```json
{
  "status": "ok",
  "checks": [
    {
      "name": "schema",
      "status": "ok"
    },
    {
      "name": "keyword_repetition",
      "status": "warning",
      "message": "Exact-match usage increased significantly."
    },
    {
      "name": "fact_check",
      "status": "needs_human_verification"
    }
  ]
}
```

Never turn validation failure into an empty suggestion list.

Distinguish:
- passed
- warning
- blocked
- needs_human_verification
- error

## Important failure guards

### Over-optimization
If keyword repetition increases substantially:
- warn or block
- do not automatically rewrite again

### Thin edits
Reject suggestions that only say:
"Add more detail."

Require:
- exact section
- exact problem
- evidence
- actual proposed text

### Factual drift
If the proposed text introduces factual claims:
- flag them
- do not claim verification without a source

### Cannibalization

Before suggesting a large expansion, inspect existing project pages/keywords where available.

If another page already targets the same intent:
recommend:
- internal link
- strengthen existing dedicated page
- or separate-page action

Do not automatically merge all related topics into one article.

### Competitor copying

Do not pass unnecessary raw competitor article text into the writing prompt.

Prefer structured evidence:
- topic
- heading
- question
- frequency
- intent
- SERP format

The AI draft must be original wording.

## Review UI

Extend the existing suggestion review UI.

Each suggestion should show:

```text
ACTION #1
Add missing B.Ed eligibility section

Evidence
6/7 comparable ranking pages cover this.

BEFORE
[existing content]

AI DRAFT
[proposed content]

Validation
✓ Structure
✓ No obvious duplication
✓ Keyword repetition acceptable
⚠ 1 factual claim needs verification

[Accept] [Edit] [Reject]
```

For edits, provide a clear before/after diff.

Do not force the user to review a full regenerated article.

## Storage

Reuse the existing `Suggestion` model/state machine wherever possible.

If additional fields are genuinely required, add a migration.

Potential fields:
- suggestion_type
- evidence_json
- validation_json
- confidence
- requires_fact_check
- claims_to_verify

Do not add redundant parallel tables if existing structures can safely hold the data.

Existing decided suggestions must survive regeneration.

Existing duplicate protection must continue working.

## WordPress

After human approval:
- use the existing WordPress route/connector
- create/update WordPress draft as appropriate
- preserve rollback
- verify the deployed change
- never publish automatically

Do not create a second WordPress API client.

## UI behavior

Use the existing server-rendered templates + HTMX patterns.

Do not introduce React/Vue.

Keep the workflow responsive:
- generate
- show validation
- review
- accept/edit/reject
- deploy

If generation is slow, use the existing job system where appropriate rather than blocking the entire request.

Do not add a new queue framework.

## Tests

Add tests for:

1. valid structured AI output
2. malformed AI JSON
3. missing required evidence
4. unsupported suggestion type
5. keyword repetition warning
6. duplicate content detection
7. factual claim flagging
8. brand/tone validation
9. cannibalization detection
10. before/after generation
11. accept
12. edit
13. reject
14. deploy
15. rollback
16. no_data SERP
17. SERP provider error/fallback
18. existing suggestion regeneration behavior
19. existing image-alt suggestion behavior remains intact
20. full existing test suite

## Explicit data-state rules

Every external dependency must distinguish:

```text
ok
no_data
error
```

Do not render:
- API failure as an empty SERP
- missing page content as a successful blank page
- failed AI generation as zero suggestions

If there is insufficient evidence, the correct result is:

```text
No reliable optimization recommendation.
```

not a generic AI suggestion.

## Non-goals

Do NOT build:
- full AI article writer
- automatic publishing
- universal SEO score
- keyword-density target
- persistent crawler
- new microservices
- Redis/Celery/Kubernetes
- second WordPress connector
- second suggestion approval workflow
- automatic competitor-content rewriting

## Definition of done

A user can:

1. select an existing page
2. select/enter a target keyword
3. fetch current SERP evidence
4. see the highest-value optimization opportunities
5. generate an atomic AI draft
6. see why the recommendation exists
7. see before/after
8. see validation results
9. accept/edit/reject
10. send the approved change through the existing WordPress workflow
11. verify the deployed change

At the end:
- run the full test suite
- report every changed file
- report migrations
- report assumptions
- report unverified live-provider behavior
- do not claim anything was tested against a live provider/site unless it actually was
