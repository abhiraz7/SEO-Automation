# Feature (future): resolving a "low text-to-page-size ratio" finding

Applies to every site the tool audits, not one customer. Status: proposed.
Written 2026-09-30 from a five-direction research pass (SEO evidence, technical
remediation, page-type policy, a content-pipeline review, and a prompt critique).

## 1. Where we are today (already shipped on this branch)

- The finding (`content` / `thin`, DataForSEO `low_content_rate`) is **unscored**: it
  never lowers Site Health (audit classification v2) and shows as an Opportunity.
- **AI suggestions are paused** for it (`issue_copy.PAUSED_AI`). The server refuses to
  generate (409, the provider is never called); the fix modal shows why. Old suggestions
  stay in the database, hidden.
- **Current value** no longer says a bare "Blank". It shows the stored word count if one
  exists, otherwise "Not measured: only DataForSEO's flag is stored". (Observed: the
  DataForSEO ingest path does not currently store `plain_text_word_count` for these pages,
  so on real data the second wording is what appears.)

## 2. What we learned

Verified (with sources in the research briefs) unless marked otherwise.

- DataForSEO's flag fires when `plain_text_size / size < 0.1`, both in bytes
  (docs.dataforseo.com/v3/on_page-pages/). It is a **markup-size metric**, not a
  thin-content metric. Whether `plain_text_size` counts bytes or characters is
  **unverified**, and matters for non-Latin scripts (3 bytes per character in UTF-8).
- Google has said, via John Mueller (secondary reports; primary pages not fetched), that
  the text-to-HTML ratio is not a ranking factor. No credible correlation study found.
  The "ideal 25-70%" figures come from SEO-tool vendor blogs.
- The only indirect angle: very bloated HTML can slow a page.
- "Thin content" in Google's guidance is about usefulness and originality, not bytes or
  word count. Padding a page to fix a ratio can create the low-value content that hurts.
- The current AI prompt forced "exactly 3 fixes", never explained the metric, passed no
  page type or word count, and had no "leave it" option, so it defaulted to "add 800-1200
  words". That advice can harm a site (padding, filler on archive/tool pages, invented
  facts, keyword stuffing, near-duplicate text across sibling pages).

## 3. Three different causes, three different fixes

1. **Markup bloat** (real text exists, HTML is heavy): inline `style="..."` on many
   elements, inline CSS/JS/SVG/base64, page-builder wrapper nesting, menus rendered twice,
   large inline JSON (schema, plugin config), related-post and widget lists, ad/tracking
   snippets. Fix by reducing markup, not adding words.
2. **Genuinely little text on a page that should have some** (a hub, tool or short
   article with almost no prose): a short human-written intro or explainer helps users.
3. **Expected for the page type** (link grids, calculators, media galleries): no action.

## 4. Decision policy (decide in code, let the AI only phrase it)

Compute the branch from data, then show one recommendation. Page type comes from URL
patterns kept as a **per-project setting** (defaults below), never hardcoded to a site.
A wrong guess degrades safely because every branch is conservative.

Signals: W = word count, R = text/size ratio, S = HTML size, L = internal links.

| Page type (default pattern) | Condition | Outcome |
|---|---|---|
| Any | noindex, canonical elsewhere, or W < 50 with L < 3 and no embed | Merge or noindex |
| Home / hub / archive (shallow path, or L >= 15 with W < 400) | W < 150 | Add a 60-120 word human-written intro; keep the list |
| Home / hub / archive | W >= 150 | Leave it (expected for link-heavy pages) |
| Tool / calculator (`/tools/`, title like calculator/converter) | W < 80 | 2-3 sentence explainer + one example |
| Tool / calculator | otherwise | Leave it |
| Document / note page (per-project prefix, or an embed is present) | W < 100 | Short summary above the embed |
| Any | W >= 300, R < 10%, S large (about 250 KB) | Reduce markup |
| Article | W < 300 | Add substantive sections (outline only, never a blanket word count) |

Thresholds above are starting points, not measured. Never say: "add 800-1200 words" on an
archive, tool or hub page; never give a word target without a benchmark.

## 5. Data to capture (mostly free)

DataForSEO already returns these in the same payload; we currently drop them:
`plain_text_rate`, `plain_text_size`, `size`, `total_dom_size`, `internal_links_count`,
`images_count`, `scripts_count`, and `plain_text_word_count`. Store them in **one JSON
`metrics` column** (a new column, so a migration the user types). Also needed: an
embed flag (iframe/PDF/`<embed>`), found with one HTML fetch only when this finding fires,
and the noindex check (confirm the exact DataForSEO check key against a real payload).

## 6. Future feature: a content workbench (the "add words" path, done safely)

Goal: when the right answer really is "add or edit text", the user does it in a safe,
guided flow where the page keeps displaying exactly as it does now. Two entry points,
one editor.

### 6.1 Manual WYSIWYG editing
- Load the page's real body content from the CMS (for WordPress, through the connector
  plugin; verify the plugin has a read/update tool for post content before building).
- Edit visually (a maintained rich-text editor such as TipTap/Quill/TinyMCE; choose on
  its handling of arbitrary HTML), with a code view for power users.
- Live preview, a before/after diff, then deploy back through the same guarded path as
  other fixes: fetch the live value right before writing (the site may have drifted),
  record a revision with rollback, and verify live afterwards.

### 6.2 "Generate a prompt" workflow (any AI, no AI cost to us)
- The tool builds a ready-to-copy prompt from the page: its text, page type, language,
  the measured facts (word count, ratio), and the outcome from section 4.
- The prompt states hard rules: keep every fact, invent none; keep the language and
  tone; add only what the outcome calls for (an intro, a summary, an outline-driven
  section), with a length tied to the evidence, not a blanket number; no keyword
  stuffing; no FAQ unless the questions come from the supplied text.
- The user pastes it into any AI, pastes the reply back into the editor, edits it, and
  deploys. Page content leaves the system only by the user's explicit copy.

### 6.3 Images and everything that must not change (the hard requirement)
The page must display as it does today. Rules:
- **Tokenise before prompting.** Replace each `<img>`/`<figure>`, embed (iframe, video,
  PDF viewer), shortcode, script, and block-editor comment with a stable placeholder such
  as `[[IMG:1]]`, `[[EMBED:2]]`. Only text and placeholders go to the AI; never send
  image data or attributes.
- **Validate on paste-back.** Every placeholder must return exactly once and in a sensible
  place; block deploy and say which is missing or duplicated.
- **Re-inject the original markup byte-for-byte** (alt, srcset, sizes, classes, lazy-load
  attributes, dimensions), so nothing about an image changes.
- **Sanitise** the pasted HTML with an allow-list; never let script or event handlers in.
- **Show the projected ratio before deploy** (text bytes / HTML bytes, computed locally)
  and re-measure after deploy with the same method. If the edit made the page longer but
  did not move the ratio, say so plainly.
- Size and safety limits; log every failure once at the decision point (no secrets, no
  draft text) using the shared failure logger.

### 6.4 Milestones (each its own branch or commit set, user verifies each)
1. Store the extra DataForSEO metrics (migration typed by the user).
2. Page-type classifier + decision policy from section 4, with tests.
3. Outcome card in the fix modal ("Leave it", "Reduce markup", "Add a short intro"),
   with no AI call except to phrase an intro.
4. Read-only content loader + tokeniser/validator with tests (placeholders round-trip).
5. WYSIWYG editor + diff + guarded deploy with rollback.
6. Prompt generator (copy button) feeding the same editor.
7. Turn AI suggestions back on for this finding, only through the outcomes above.

## 7. Open questions
- Does the connector expose read and write of full post content, and does an update ever
  strip markup the caller did not send? (The plugin changelog mentions such a fix.)
- Is `plain_text_size` bytes or characters for non-Latin text? Verify with one real page:
  strip tags, `wc -c` and `wc -m` against DataForSEO's number.
- Where does the per-project page-type pattern list live (project settings)?
- Non-WordPress sites: is this feature WordPress-only at first? (Recommended: yes.)
- Editor choice, and whether pasted content needs a moderation/AI-disclosure note.
- Units: show ratio in bytes (matches the metric) plus words (what people understand).

## 8. Out of scope
Scoring changes (the finding stays unscored); auto-applying edits without the user's
review; generating page copy inside the app without an explicit user action.
