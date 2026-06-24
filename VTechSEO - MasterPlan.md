# VTechSEO - SOLO DEVELOPER MASTERPLAN

## Rules

1. Logic First
2. Product First
3. No Fancy Infrastructure
4. No Microservices
5. No Kubernetes
6. No Redis
7. No Celery
9. No Multi-Model Arena
10. Claude writes code, I review and test
11. Push every working feature to GitHub https://github.com/abhiraz7/SEO-Automation.git
12. Finish one feature completely before starting next
13. At the end of every update, Mandatory update what changes we did in Agentlog.md md file,So that we preserve session.
---

# Architecture

Browser
↓
FastAPI
↓
Postgresql

FastAPI
↓
Claude API

FastAPI
↓
Supabase()

---

# Tech Stack

Backend

* FastAPI

Database

* SQLite

AI

* Claude API

Learning Dataset

* Supabase



UI

* FastAPI Templates
* HTMX
* Tailwind

---

# Core Product

Connected Site

Manual Project

---

# Database Tables

projects

pages

crawl_snapshots

issues

suggestions

judge_results

deployment_logs

---

# Supabase Tables

acceptance_dataset

judge_dataset

visibility_dataset

memory_dataset

---

# Development Cycle

Feature
↓
Tasks
↓
Implementation
↓
Manual Testing
↓
Bug Fix
↓
Git Commit
↓
Next Feature

---

# Phase 1

Feature

Website Crawl

## Tasks

Create Project

Create Crawl Engine

Store Page Data

Store Snapshots

Display Crawl Results

## Tests

Valid Site

Invalid Site

Large Site

Broken Site

Redirect Site

## Done When

Website crawls successfully.(Manual)

---

# Phase 2

Feature

SEO Audit

## Tasks

Page Title Rules

Meta Description Rules

H1 Rules

H2 Rules

Image Alt Rules

Schema Rules

Canonical Rules

OpenGraph Rules

Twitter Rules

Lang Rules

Content Rules

## Tests

Missing Fields

Duplicate Fields

Length Validation

Schema Validation

## Done When

All issues detected.

---

# Phase 3

Feature

AI Suggestions

## Tasks

Claude Integration

Generate 5 Suggestions

Store Suggestions

Display Suggestions

## Tests

Success

Failure

Timeout

Duplicate Suggestions

## Done When

Every issue receives suggestions.

---

# Phase 4

Feature

Rule Validation

## Tasks

Length Validation

Keyword Validation

Uniqueness Validation

Readability Validation

## Tests

Pass

Fail

Boundary Cases

## Done When

Suggestions validated.

---

# Phase 5

Feature

LLM Judge

## Tasks

Judge Prompt

Score Suggestions

Store Scores

Rank Suggestions

## Tests

Good Suggestions

Bad Suggestions

Judge Consistency

## Done When

Best suggestion identified.

---

# Phase 6

Feature

Acceptance Tracking

## Tasks

Track Accept

Track Reject

Track Edit

Track Deploy

Sync To Supabase

## Tests

Accept Event

Reject Event

Edit Event

Deploy Event

## Done When

User actions stored.

---

# Phase 7

Feature

Learning Dataset

## Tasks

Store Judge Data

Store Acceptance Data

Store Visibility Data

Store Memory Data

## Tests

Insert

Read

Duplicate Check

## Done When

Dataset grows automatically.

---

# Phase 8

Feature

RivalFlow

## Tasks

Find Competitors

Extract Terms

Extract Questions

Extract Sections

Gap Analysis

## Tests

Competitor Discovery

Gap Detection

Output Accuracy

## Done When

Missing content identified.

---

# Phase 9

Feature

RAG

## Tasks

Generate Embeddings

Similarity Search

Retrieve Examples

Prompt Injection

## Tests

Retrieve Relevant Examples

Prompt Quality

Suggestion Improvement

## Done When

Historical wins improve suggestions.

---

# Phase 10

Feature

AI Visibility Prediction

## Tasks

Visibility Analysis

Brand Detection

Competitor Detection

Visibility Scoring

## Tests

Brand Mention

Competitor Mention

No Mention

## Done When

Visibility score generated.

---

# Phase 11

Feature

WordPress Deploy

## Tasks

Deploy Changes

Rollback Changes

Deployment Logs

## Tests

Successful Deploy

Failed Deploy

Rollback

## Done When

One-click deployment works.

---

# Success Criteria

Website
↓
Crawl
↓
Audit
↓
Generate
↓
Validate
↓
Judge
↓
Accept
↓
Learn

Works end-to-end.

Only then add new features.
