# SEO AI Platform - Versioning Roadmap

## Core Product Vision

Build a self-hosted AI SEO platform that combines:

* Website Crawling
* SEO Auditing
* AI Suggestions
* AI Evaluation
* User Acceptance Tracking
* Learning Dataset
* Future RAG Enhancement

The long-term moat is not the UI.

The moat is:

Issue
+
Suggestion
+
Judge Score
+
Visibility Score
+
User Acceptance
+
Deployment Outcome

Collected over thousands of optimizations.

---

# Architecture Freeze

Backend

* FastAPI

Database

* SQLite

AI

* Claude API

Learning Dataset

* Supabase

Deployment

* Docker

---

# Product Types

## Connected Site

WordPress site connected using our plugin.

Capabilities:

* Crawl
* Audit
* Generate Fixes
* Deploy Fixes
* Rollback
* Track Improvements

## Manual Project

Any website URL.

Capabilities:

* Crawl
* Audit
* Generate Fixes
* Judge Fixes
* Track Progress

No deployment.

---

# Core Workflow

Crawl
↓
Audit
↓
Generate 5 Suggestions
↓
Rule Validation
↓
LLM Judge
↓
User Selects
↓
Acceptance Tracking
↓
Learning Dataset

---

# V1 - Crawl Engine

Goal:

Collect page data.

## Crawl Fields

Page Title

Meta Description

H1 Length

H2 Length

Heading Structure

Image Alt Text

Meta Keywords

Domain Schema

Page Schemas

Canonical Link

OG Description

OG Title

OG URL

Twitter Description

Twitter Title

Twitter Site

Twitter Card

Lang Attribute

Custom HTML Content

## Deliverables

Single Page Crawl

Full Site Crawl

Snapshot Storage

---

# V1.5 - Audit Engine

Goal:

Detect SEO issues.

## Rules

Page Title

* Missing
* Too Short
* Too Long
* Duplicate

Meta Description

* Missing
* Too Short
* Too Long

H1

* Missing
* Multiple H1
* Too Short
* Too Long

H2

* Missing
* Poor Structure

Image Alt

* Missing
* Empty

Schema

* Missing
* Invalid

Canonical

* Missing

OpenGraph

* Missing

Twitter Cards

* Missing

Lang Attribute

* Missing

Content

* Thin Content

---

# V2 - Snapshot System

Goal:

Track changes over time.

## Features

Snapshot 1

Snapshot 2

Snapshot 3

Compare Results

## Outputs

Fixed Issues

New Issues

Regressions

SEO Score Change

---

# V3 - AI Suggestions

Goal:

Generate SEO improvements.

## Features

Claude Integration

Generate 5 Suggestions

Store Suggestions

Suggestion History

---

# V4 - Rule Validation

Goal:

Validate AI output.

## Validation

Length

Keyword Presence

Uniqueness

Readability

Structure

---

# V5 - LLM Judge

Goal:

Score suggestions.

## Judge Criteria

SEO Quality

CTR Potential

Keyword Usage

Relevance

Clarity

## Output

Score

Reason

Rank

---

# V6 - Acceptance Tracking

Goal:

Collect user decisions.

## Track

Accepted

Rejected

Edited

Deployed

Timestamp

---

# V7 - Learning Dataset

Goal:

Build proprietary data.

## Supabase Tables

judge_dataset

acceptance_dataset

visibility_dataset

memory_dataset

---

# V8 - RivalFlow

Goal:

Find content gaps.

## Input

My Page

Keyword

## Process

Find Competitors

Extract Terms

Extract Questions

Extract Sections

Compare Content

## Output

Missing Terms

Missing Questions

Missing Sections

Heading Suggestions

Meta Suggestions

---

# V9 - RAG

Goal:

Learn from previous wins.

## Process

Issue
↓
Find Similar Accepted Examples
↓
Inject Into Prompt
↓
Generate Better Suggestions

---

# V10 - AI Visibility Prediction

Goal:

Estimate AI search visibility.

## Output

Visibility Score

Missing Signals

Improvement Suggestions

Confidence Score

---

# V11 - WordPress Deploy

Goal:

One-click fixes.

## Features

Deploy

Rollback

Deployment Logs

Version History
