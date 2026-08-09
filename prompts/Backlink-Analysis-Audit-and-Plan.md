You are the lead software architect for VTechSEO.

Repository:
https://github.com/abhiraz7/SEO-Automation

IMPORTANT:
Before changing any code, inspect the existing repository thoroughly.
Do NOT rebuild existing functionality.
Do NOT introduce a second architecture where an existing abstraction already works.
Do NOT remove working functionality.
Do NOT add Crawl4AI or make website crawling a dependency of the current Keyword/Backlink intelligence system. We pivoted away from that approach.

==================================================
PRODUCT VISION
==================================================

VTechSEO is an AI-powered SEO Decision Engine.

The goal is NOT to build separate "Keyword Tool" and "Backlink Tool" products.

The goal is to automate the repetitive research, analysis and recommendation work normally performed by a digital marketing analyst.

The workflow is:

COLLECT
→ NORMALIZE
→ ANALYZE
→ FIND OPPORTUNITIES
→ PRIORITIZE
→ EXPLAIN
→ RECOMMEND
→ HUMAN DECISION

The human remains in control.

AI must never silently make or deploy a decision.

The UI should feel familiar to users of SEMrush/Ahrefs-style SEO platforms, but VTechSEO adds an AI intelligence layer on top.

The product should answer:

"What should I do next, why should I do it, and what evidence supports that recommendation?"

==================================================
CORE PRINCIPLE
==================================================

Separate FACTS from ANALYSIS from AI REASONING.

FACTS:
SEMrush / DataForSEO / project data

DETERMINISTIC ANALYSIS:
Python/business logic

AI REASONING:
Claude / Gemini

HUMAN DECISION:
Accept / Reject / Edit / Ignore / Save

Never ask an LLM to calculate something that can be calculated deterministically.

Never use an LLM as the source of truth for:
- search volume
- keyword difficulty
- rankings
- backlink counts
- referring domains
- SERP positions
- dates
- percentages
- provider metrics

The APIs provide evidence.
Our application analyzes the evidence.
AI interprets the evidence and produces recommendations.

==================================================
EXTERNAL DATA PROVIDERS
==================================================

Current primary providers:

1. SEMrush API
2. DataForSEO API

AI providers:

3. Claude
4. Gemini

These are provider adapters.

Do not tightly couple business logic to a specific provider.

Use this architecture:

Provider
→ normalized result
→ analysis engine
→ AI reasoning
→ recommendation

If SEMrush and DataForSEO both provide the same capability, the application should be able to switch providers without rewriting the UI/business logic.

==================================================
NORMALIZED DATA LAYER
==================================================

Create/maintain normalized internal schemas.

Examples:

NormalizedKeyword
- keyword
- location
- language
- volume
- trend
- difficulty
- cpc
- competition
- intent
- serp_features
- source
- fetched_at

NormalizedSERPResult
- keyword
- position
- url
- domain
- title
- description
- result_type
- serp_features
- source
- fetched_at

NormalizedBacklink
- target_url
- source_url
- referring_domain
- anchor
- follow_type
- first_seen
- last_seen
- authority metrics
- country
- source

NormalizedReferringDomain
- domain
- authority
- backlinks
- referring_pages
- country
- tld
- first_seen
- last_seen

Never expose raw provider-specific response structures directly to the UI.

==================================================
SEO ANALYSIS ENGINE
==================================================

The analysis engine must be deterministic wherever possible.

Build reusable analysis modules.

Suggested structure:

app/
    providers/
        semrush/
        dataforseo/
    analysis/
        keyword_analysis.py
        serp_analysis.py
        backlink_analysis.py
        competitor_analysis.py
        opportunity_engine.py
    ai/
        claude.py
        gemini.py
        seo_reasoning.py
    models.py
    routes/
    services/

Do not blindly follow these filenames if the repository already has an equivalent structure.
Reuse existing architecture where appropriate.

==================================================
KEYWORD INTELLIGENCE
==================================================

Build a complete SEMrush-level keyword research capability using available APIs.

Capabilities:

1. Keyword Overview
2. Keyword Discovery
3. Related Keywords
4. Long-tail Keywords
5. Questions
6. Keyword suggestions
7. Bulk Keyword Analysis
8. Keyword Lists
9. Keyword Clustering
10. SERP Analysis
11. Competitor Keyword Gap
12. Local Keyword Research
13. Keyword Trends
14. Search Intent
15. SERP Feature analysis
16. Keyword opportunity scoring
17. AI recommendations

Do not attempt to recreate SEMrush's proprietary keyword database.

Use SEMrush/DataForSEO as data providers.

Our differentiation is the decision layer.

==================================================
KEYWORD OVERVIEW
==================================================

For every keyword, show where available:

- Search volume
- Trend
- Keyword difficulty
- CPC
- Competition
- Search intent
- Location
- Language
- SERP features
- Current project ranking
- Competitor rankings

Then calculate:

- Business relevance
- Ranking feasibility
- Traffic potential
- SERP opportunity
- Competitive gap
- Overall opportunity score

The opportunity score must be explainable.

Do not create a mysterious AI score.

Example:

Opportunity Score: 84/100

Factors:
Business relevance: 92
Ranking feasibility: 76
SERP opportunity: 88
Traffic potential: 81
Competitive gap: 85

==================================================
SERP INTELLIGENCE
==================================================

For a selected keyword, analyze available SERP data.

For each result:

- Position
- URL
- Domain
- Title
- Description where available
- Result type
- SERP feature
- Domain information
- Ranking status

Detect where possible:

- weak results
- irrelevant results
- duplicate domains
- duplicate/near-duplicate URLs
- SERP intent
- content-type distribution
- SERP feature opportunities
- forums
- Reddit/community results
- PAA
- related searches
- local pack
- video
- shopping
- featured snippets
- other provider-supported SERP features

Do not use an LLM for basic detection.

Use deterministic rules first.

AI can interpret the resulting facts.

==================================================
KEYWORD CLUSTERING
==================================================

Do NOT rely only on lexical/root-word clustering.

Where SERP data is available, support SERP-based clustering.

Principle:

If multiple keywords have substantially overlapping SERPs, they are likely candidates for the same page/topic.

If their SERPs differ substantially, they may represent different search intents and should potentially be separate pages.

The system should explain why keywords were grouped.

==================================================
COMPETITOR KEYWORD GAP
==================================================

Support:

- client domain
- competitor domains
- shared keywords
- missing keywords
- weak rankings
- competitor wins
- ranking gaps

Classify:

UNTAPPED
Client doesn't rank.

WEAK
Client ranks but competitors substantially outperform.

SHARED
Multiple domains rank.

STRONG
Client outperforms competitors.

LOST/DECLINING
Historical ranking deterioration where data is available.

Then calculate opportunity.

==================================================
KEYWORD STRATEGY
==================================================

Allow users to turn keyword research into a strategy.

Example:

PILLAR:
Dental Services in Patna

    ├── Dental Implants
    │   ├── Dental implant cost
    │   ├── Dental implant procedure
    │   └── Dental implant recovery
    │
    ├── Braces
    └── Root Canal

The AI should help determine:

- same page vs new page
- primary keyword
- secondary keywords
- search intent
- priority
- rationale

Human must approve the recommendation.

==================================================
BACKLINK INTELLIGENCE
==================================================

Current implementation already has backlink functionality.

Do NOT rebuild existing functionality.

Audit it first.

Current capabilities include:

- referring domains
- backlink count
- anchor text
- new/lost/active backlink tracking

The major missing capability identified in the audit is:

COMPETITOR BACKLINK GAP.

Implement:

1. Competitor discovery where supported
2. Competitor list management
3. Domain intersection
4. Bulk referring-domain analysis
5. Link gap
6. Link opportunity scoring
7. AI interpretation

==================================================
COMPETITOR MODEL
==================================================

Create ONE shared competitor-list concept.

Do not create separate competitor tables for:

- Keyword Gap
- Backlink Gap
- RivalFlow

All competitor-related features must use the same project-level competitor data.

Example:

Project
    ↓
Competitors
    ├── competitor A
    ├── competitor B
    └── competitor C

Keyword Gap and Backlink Gap consume the same list.

==================================================
BACKLINK GAP
==================================================

Use DataForSEO's backlink domain-intersection capability where appropriate.

Concept:

Competitors
    ↓
Domains linking to competitors
    ↓
Exclude client's referring domains
    ↓
Rank opportunities
    ↓
Explain opportunity

Example:

IndustrySite.com

Competitor A: yes
Competitor B: yes
Competitor C: yes
Client: no

This is a high-value link-gap candidate.

Calculate deterministic signals such as:

- number of competitors linked
- authority
- country
- TLD
- backlink count
- link type
- existing client relationship
- other available provider metrics

Then AI can classify likely acquisition opportunity:

- HIGH
- MEDIUM
- LOW

and explain why.

AI must not invent facts.

==================================================
AI SEO REASONING LAYER
==================================================

Claude and Gemini are interchangeable AI providers.

Create a provider-neutral interface.

Example conceptual contract:

generate(
    prompt,
    provider,
    model=None
) -> LLMResult

LLMResult:

- ok
- text
- error
- provider
- model
- usage if available

Existing Claude behavior must remain working.

Gemini should be optional.

Do not duplicate entire application logic for Gemini.

==================================================
AI ROLE
==================================================

AI receives a COMPACT EVIDENCE PACKAGE.

Do not send huge raw datasets.

Example keyword AI context:

{
  keyword,
  business_context,
  metrics,
  current_position,
  competitor_positions,
  serp_summary,
  serp_features,
  opportunity_factors,
  content_gaps,
  trend
}

AI should return structured output.

Example:

{
  "opportunity": "high",
  "recommendation": "...",
  "why": [
      "...",
      "..."
  ],
  "recommended_action": "...",
  "priority": "high",
  "confidence": 0.84
}

Never allow free-form AI text to be the only source of truth.

==================================================
AI RECOMMENDATION PRINCIPLE
==================================================

The system should answer:

WHAT?
WHY?
EVIDENCE?
PRIORITY?
WHAT SHOULD THE HUMAN DO?

Example:

Keyword:
"dental implants patna"

Opportunity:
HIGH

Why:
- Client ranks #18
- Competitors rank top 5
- Search demand is increasing
- SERP is dominated by local commercial pages
- Several competitors have weak topical coverage

Recommendation:
Optimize existing page rather than creating a new page.

Priority:
HIGH

The human can:

[Accept]
[Edit]
[Reject]
[Save]
[Ignore]

AI does NOT automatically deploy changes.

==================================================
BUSINESS PROFILE
==================================================

Every project can have:

- Business Name
- Services
- Audience
- City
- Region
- Country
- Brand Tone

This context should influence AI recommendations.

Example:

Same keyword:

"best coaching institute"

Patna recommendation
must not be identical to
New York recommendation.

Do not hardcode geographic assumptions.

==================================================
UI PHILOSOPHY
==================================================

Use a familiar SEMrush-style information architecture.

Do NOT copy SEMrush branding or proprietary UI.

The user should understand:

Projects
SEO Dashboard
Keyword Research
Backlinks
Site Audit
Position Tracking
Competitors
Reports
Settings

But VTechSEO adds AI intelligence on top.

Traditional metrics remain visible.

AI recommendations should appear alongside them.

==================================================
HUMAN-IN-THE-LOOP
==================================================

Every meaningful AI recommendation must allow:

- Accept
- Reject
- Edit
- Save
- Ignore

The human remains the decision-maker.

Do not auto-deploy AI recommendations.

==================================================
DASHBOARD
==================================================

The project dashboard should not merely show metrics.

Show:

SEO Health
Traffic
Organic Keywords
Visibility
Backlinks
Referring Domains

PLUS:

AI Opportunities

Example:

12 opportunities found

4 Technical
3 Content
2 Keyword
3 Backlink

Each opportunity should contain:

- problem/opportunity
- evidence
- priority
- recommended action
- confidence
- human decision state

==================================================
COST CONTROL
==================================================

Do not call AI for every row or every page.

Use this pipeline:

Provider data
→ deterministic analysis
→ filter/prioritize
→ compact context
→ AI only for valuable opportunities

Cache AI results.

Re-run AI only when relevant underlying data changes.

Track:

- provider calls
- AI calls
- token usage where available
- errors
- cache hits

==================================================
DATABASE
==================================================

Keep SQLite for current development unless there is a concrete reason to migrate.

Do not migrate databases merely for architectural fashion.

Design models so future PostgreSQL migration is straightforward.

Use normalized tables for:

- projects
- business profiles
- competitors
- keywords
- keyword snapshots
- SERP results
- keyword clusters
- backlinks
- referring domains
- competitor backlink gaps
- opportunities
- AI recommendations
- provider connections
- jobs

Do not store giant raw API responses unnecessarily.

Store only useful normalized data and selected raw evidence where required for debugging/auditing.

==================================================
SCHEDULER
==================================================

Use the existing scheduler architecture.

Jobs should be modular.

Examples:

keyword_pull
serp_pull
position_pull
backlink_pull
competitor_keyword_pull
competitor_backlink_pull
opportunity_analysis

Do not create one giant scheduler job that performs everything.

==================================================
ERROR HANDLING
==================================================

Provider responses must have explicit states:

OK
NO_DATA
ERROR

Never silently convert provider failures into empty results.

UI should distinguish:

"No data found"

from:

"Provider failed"

from:

"Not yet fetched"

==================================================
PRODUCTION PRINCIPLES
==================================================

Do not:

- duplicate provider logic
- duplicate competitor models
- mix API-specific response structures into templates
- call LLMs unnecessarily
- hide errors
- hardcode API keys
- hardcode locations
- make AI responsible for deterministic calculations
- rebuild already-working modules
- introduce unnecessary dependencies
- build features simply because competitors have them

Every feature must have a clear reason.

==================================================
IMPLEMENTATION METHOD
==================================================

IMPORTANT:

DO NOT implement this entire architecture in one giant change.

Work as an architect.

STEP 1:
Audit the existing repository.

Produce:

1. Current architecture
2. Existing modules
3. Existing provider adapters
4. Existing database models
5. Existing keyword functionality
6. Existing backlink functionality
7. Existing scheduler
8. Existing AI integration
9. Existing UI
10. Missing pieces
11. Duplicated functionality
12. Technical debt
13. Recommended implementation order

STEP 2:
Create a small implementation plan.

Every task must be independently testable.

Each task should modify the minimum number of files necessary.

STEP 3:
Implement one task.

STEP 4:
Run tests / py_compile / application boot checks.

STEP 5:
Explain:

- what changed
- why
- system design
- data flow
- what I learned
- what remains

Then STOP and wait for approval before moving to the next architectural task.

==================================================
TEACHING REQUIREMENT
==================================================

After every meaningful implementation step, explain the system design to me in simple technical terms.

Use:

INPUT
→ PROCESS
→ OUTPUT

For example:

DataForSEO
→ normalized SERP model
→ SERP analysis

Then:

SERP analysis
→ compact evidence package
→ Gemini/Claude

Then:

AI reasoning
→ recommendation
→ human decision

I want to understand the architecture while building it, not just have Claude write code.

==================================================
FIRST TASK
==================================================

DO NOT CODE YET.

First inspect the repository and produce:

# VTechSEO Architecture Audit

1. What exists today
2. What already works
3. What is partially implemented
4. What is missing
5. What should NOT be rebuilt
6. Current SEMrush capabilities
7. Current DataForSEO capabilities
8. Current Claude capabilities
9. Current Gemini/provider architecture
10. Current Keyword Research coverage
11. Current Backlink coverage
12. Current Competitor architecture
13. Current scheduler architecture
14. Database architecture
15. UI architecture
16. Technical debt
17. Recommended LEGO modules
18. Recommended build order

Then propose the FIRST small implementation task only.

Do not make code changes until this audit is complete and reviewed.