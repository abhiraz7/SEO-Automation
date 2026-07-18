# Keyword Research — User Guide

For VTechys marketing workers. No SEO background or training required — this
page explains every button, badge, and number on the Keyword Research tool.

Open it at **`/keywords`**. If your company has more than one client/site set
up, you'll land on a picker first — click into the workspace you want.

---

## 1. The top bar

| Control | What it does |
|---|---|
| **Market dropdown** (top right) | Which country's search data you're looking at (India, US, UK, …). Change it and re-run your search — the *same* keyword can have wildly different volume in different markets. Always double-check this before telling a client "nobody searches for this." |
| **Export CSV** | Downloads every keyword you're tracking in this workspace as a spreadsheet (Excel-safe, works with Hindi/regional keywords too). |
| **+ Add Keywords** | Opens a box where you paste keywords (one per line) to start tracking them long-term. Tracked keywords appear in the **Overview** tab and get their history saved automatically. |

### The status banner

If you see a small yellow or red bar near the top saying something like
*"DataForSEO: not configured"* or *"Please verify your account"* — that's the
tool telling you one of its two data providers isn't working right now.
**This is not your fault and not a bug** — it means the API keys need
attention (an admin task). The tool keeps working on whichever provider *is*
healthy; you'll just see fewer bells and whistles (e.g. no SERP feature
icons) until it's fixed. Click the **×** to dismiss it for your session, or
**Verify Account →** if shown, which takes you to the provider's site.

---

## 2. The stat cards

Four numbers at a glance for whatever you're currently tracking:

- **Tracked Keywords** — how many keywords you're actively watching in this workspace.
- **Total Search Volume** — combined monthly searches across all of them. Useful for "how big is this opportunity" conversations with a client.
- **Easy Wins** — how many of your tracked keywords scored 🟢 (Worth It ≥ 7.5). These are your best next moves.
- **Avg. Difficulty** — average Keyword Difficulty (0–100) across everything you're tracking. Lower is easier to rank for.

---

## 3. The five tabs

### Overview
Every keyword you're actively tracking, with live metrics. This is your
working list — add keywords here once you've decided they matter, so their
volume/trend gets saved over time instead of re-looked-up from scratch.

### Suggestions
Turn one seed idea into a real keyword list. Type a seed (e.g. `dentist`),
tick which kinds of ideas you want, and hit Search:

- **Related** — terms people search alongside your seed.
- **Questions** — actual questions people type into Google about it (great for FAQ pages and blog ideas).
- **Prepositions** — phrases like "dentist **for** kids", "dentist **near** me", "dentist **in** [city]" — these usually reveal *where/who/what* angle to target.
- **Comparisons** — "**best** dentist", "dentist **vs** orthodontist" — comparison/review-intent searches, good for buyer's-guide content.

Results appear grouped by type in one table so you can scan all four angles
at once.

### Bulk Analysis
Already have a list of 100 keywords from a client brief or a spreadsheet?
Paste them all (one per line) and click **Analyze** — you get volume,
intent, and Worth It score for every one in a single pass. If you paste more
than 100, you'll see a warning and only the first 100 run (that's a provider
limit, not a tool bug — split the rest into a second batch).

### Clustering
Groups your tracked + saved keywords by their shared "root" word — e.g. all
the *dentist* keywords bucket together, all the *invisalign* ones bucket
together. Useful for deciding "this is one page, that is a different page"
when planning site content, without doing it by hand.

### Saved List
Your shortlist. Anything you've hit ♡ Save on lives here permanently,
separate from what you're actively tracking — think of it as a "maybe later"
folder distinct from your active watch list.

---

## 4. Reading a keyword row

| Column | Meaning |
|---|---|
| **Keyword** | The search phrase. A yellow "No data" badge means both data providers came back empty for this keyword+market — genuinely nothing to show, not an error. A red "Lookup failed" badge means the *check itself* broke (bad API key, network, rate limit) — retry later, this is not the same as "no data." |
| **Volume** | Estimated monthly searches in the selected market. A tiny squiggly line next to it (when present) is a 12-month trend at a glance — rising, falling, or seasonal. |
| **Intent** | What the searcher actually wants, color-coded: 🟢 **Transactional** (ready to buy/book), 🟠 **Commercial** (comparing options), 🔵 **Informational** (researching), 🟣 **Navigational** (looking for a specific brand/site), 🔷 **Local**. |
| **Worth It** | See section 5 below — this is the tool's headline feature. |
| **Trend** (Overview tab only) | ▲ Rising / ▼ Falling / — Stable, computed from your own tracking history. Shows "Pending" until a keyword has been tracked long enough (at least ~2 checks a week+ apart) to compare. |

---

## 5. Worth It score — the most important number on the page

Raw "Keyword Difficulty" (a 0–100 number Semrush/DataForSEO give you) doesn't
tell you whether a keyword is actually worth your time — a keyword can be
*easy* to rank for and still useless if nobody clicks through because Google
shows an AI Overview or four ads above the organic results.

**Worth It** combines four things into one 0–10 score so you don't have to
do that math yourself:

1. **How many people search it** (volume)
2. **How hard it'd be to rank** (difficulty)
3. **How likely a click turns into a lead** (search intent — a "buy now" search is worth more than a "just curious" one)
4. **How much competition sits above the organic results** (AI Overview, ads, map pack — these steal clicks even from a #1 ranking)

You get a color-coded verdict:

- 🟢 **Easy Win** (score ≥ 7.5) — good volume, low competition, buyer-ready intent. Go after these first.
- 🟡 **Medium** (4.0–7.4) — worth doing, but expect real work (content + maybe some backlinks) to rank.
- 🔴 **Avoid** (< 4.0) — low volume, brutal competition, or an intent that won't convert. Don't waste a content budget here unless there's a strategic reason.

**Click the score** (or the keyword itself) to expand the row and see
*exactly why* it scored that way — the plain-English breakdown, e.g.:

> Strong volume (90,500/mo)
> Medium difficulty (KD 56) — needs good content + some links
> Transactional intent — searcher is ready to act
> AI Overview present — expect fewer organic clicks

That explanation is your talking point when you have to justify a content
priority to a client or a manager.

> **Note:** if the SERP hasn't been checked yet (or the SERP provider is
> temporarily down), the score is computed *without* the competition factor
> and the explanation will say so honestly — it won't pretend the SERP is
> clean. Expand the row to force a real check.

---

## 6. The expand row (👁 / click a keyword)

Click any keyword or its Worth It pill to open a detail panel in place —
no page navigation, no losing your spot in the list. You get:

- **Metrics** — volume, difficulty, CPC, intent, sparkline.
- **Worth It breakdown** — the full factor list from section 5.
- **SERP Features** — icon chips showing what's competing for the click:
  - 🤖 **AI Overview** — Google's AI answer box is shown; organic clicks drop a lot.
  - 📦 **Ads ×N** — how many paid ads sit above the organic results.
  - ⭐ **Featured Snippet** — someone already owns "position zero."
  - ❓ **PAA** ("People Also Ask") — an expandable question box eating attention.
  - 📍 **Maps** — a local map pack is shown (very relevant for local-business clients).
  - 📹 **Video** / 🖼 **Images** / 🛒 **Shopping** — rich results present.
  - If none of these show, you'll see *"Clean SERP — no features competing for clicks"* — the best-case scenario.
- **Top Ranking Results** — the current top 10 organic URLs, clickable, so you can see exactly who you're competing against.
- **Questions People Ask** — real question-phrase keywords related to this one, ready to drop into an FAQ section.
- **⚡ Generate Content Brief button** — see section 7.

---

## 7. ⚡ Generate Content Brief (AI-powered)

The one-click answer to "okay, Worth It says this is a good keyword — now
what do I actually write?"

Click **⚡** on any row (or the button inside the expand panel) and the tool
sends the keyword's real data — its metrics, the actual top-10 competitors,
and real questions people ask — to Claude, which writes back a ready-to-use
brief in about 15–20 seconds:

- **Search Intent** — what the searcher actually wants, in plain language.
- **Angle To Win** — what the current top-ranking pages are missing, i.e. your opening.
- **Suggested Title**
- **Outline** — the headings the article should have.
- **FAQs To Answer** — pulled from real "people also ask" questions.
- **AI-Visibility Tips** — how to structure the page so AI answer engines (like Google's AI Overview) are more likely to cite it.

Click **📋 Copy** to grab the whole brief and hand it straight to a writer.
Every brief is generated fresh from live data — it costs the company a
fraction of a cent per brief, so use it freely.

---

## 8. Row actions (hover over a row)

Hover any row to reveal quick-action icons on the right:

| Icon | Action |
|---|---|
| 👁 | Open the detail/expand panel (same as clicking the keyword) |
| ⚡ | Generate a content brief |
| 📋 | Copy the keyword text to your clipboard |
| 📈 | Track this keyword (adds it to your Overview list) — only shown for keywords you're not already tracking |
| ♡ / ❤️ | Save to your Saved List (heart fills in once saved) |
| ✕ | Untrack (Overview tab only) — removes it and its history, with a confirmation prompt |

---

## 9. Filters (above the Overview/Suggestions/Bulk tables)

Narrow down a long list without scrolling:

- **Intent dropdown** — show only Transactional, Commercial, etc.
- **Min volume** — hide anything below a search-volume floor.
- **Max KD** — hide anything above a difficulty ceiling.
- **Include word** — only keywords containing a specific word.
- **Exclude word** — hide keywords containing a specific word (handy for filtering out irrelevant results, e.g. excluding "jobs" from a "dentist" search).
- **🟢 Easy Wins only** — one click to see nothing but your best opportunities.
- **Clear** — resets every filter.

Filters apply live as you type/select — no separate "apply" button needed.

---

## 10. Charts (Overview tab)

Two small bar charts above the table give you an at-a-glance shape of your
keyword list:

- **Keyword Intent** — how many tracked keywords fall into each intent bucket. A list that's all "Informational" and no "Transactional" is a red flag for a client focused on leads/sales.
- **Volume Distribution** — how many keywords are low volume (0–100) vs. high volume (10K+). Helps you spot if you're chasing only long-tail or only head terms.

---

## 11. Bulk actions (select multiple rows)

Tick the checkbox on any row (or the header checkbox to select everything
currently visible) and a dark action bar appears at the top:

- **♡ Save** — save all selected keywords at once.
- **📈 Track** — start tracking all selected keywords at once.
- **⬇ Export CSV** — download just the selected rows as a spreadsheet.
- **✕ Untrack** — (Overview tab only) remove all selected tracked keywords, with a confirmation prompt.
- **Clear** — deselect everything.

This is the fast path once you've bulk-analyzed or generated 50+ suggestions
and only want to act on a handful.

---

## 12. Quick reference — what to do when

| You want to… | Do this |
|---|---|
| Find new keyword ideas from one topic | **Suggestions** tab → type seed → tick modes → Search |
| Check a client's existing keyword list | **Bulk Analysis** tab → paste list → Analyze |
| Know if a keyword is worth chasing | Look at its **Worth It** score/badge |
| Understand *why* it scored that way | Click the score to expand → read the factor list |
| See who you're actually competing against | Expand row → **Top Ranking Results** |
| Get a ready-to-write brief | Expand row (or hover) → **⚡ Generate Content Brief** |
| Group keywords into page/content ideas | **Clustering** tab → Generate Clusters |
| Build a long-term watch list | **+ Add Keywords** or 📈 track from any tab |
| Keep a "maybe later" shortlist | ♡ **Save** from any tab → check **Saved List** |
| Hand a spreadsheet to a client | **Export CSV** (top bar) or select rows → **⬇ Export CSV** |
| Narrow a huge list down fast | Filters bar above the table |

---

*If something looks broken (blank rows with no badge, a tab that won't
load), check the status banner first — it usually explains which provider
is down. If the banner is clean and something's still wrong, flag it to
your admin/developer rather than guessing.*
