# Deploy Engine: Findings and Plan (ExamNotesPDF, 2026-09-26)

**Site studied:** https://examnotespdf.in (project_id 9)
**Status:** Research done, plan agreed in principle. No app or plugin code changed yet.
**Goal:** turn the deploy path (app + AI SEO Connector plugin) into a trustworthy engine for many client sites: every fix lands on the right object, in the right SEO plugin's fields, and is only called "deployed" once the live page proves it.

---

## 1. What we found (all verified unless marked)

### 1.1 Deploys reported success but never went live (serious)
- Until the plugin update, deploys went through `yoast_set_meta`, which wrote `_yoast_wpseo_*` keys. ExamNotesPDF runs **Rank Math**, which reads `rank_math_*`. The values were saved but never shown.
- Comparison of all 16 past deploys for project 9 with the live pages (2026-09-26): **16 of 16 do not match**.
- The live pages show the site's own values. Stored `rank_math_title` equals the live `<title>` on every post that has one, so **Rank Math itself is working correctly**.
- The dashboard still said "deployed", because it trusted the plugin's OK response and never checked the public page. This is the most serious gap.

### 1.2 The plugin is now fixed for posts, but not proven end to end
- ai-seo-connector has detected Yoast vs Rank Math since 1.2.0 (2026-09-23). Live `/ping` on examnotespdf.in: version **1.5.0**, `seo_plugin: rankmath`.
- The set path writes the right `rank_math_*` keys, merges robots flags, and purges page caches.
- **Not yet proven:** a real post write showing up on the live page.
- `vseo.vtraffic.io` still runs the old **VtechSEO Agent 1.0.0** (Yoast only). It has the original problem until upgraded.

### 1.3 Taxonomy (term) pages cannot be deployed
- Examples: `/subject/ctet-evs/` (term 26, taxonomy `subject`), `/exam/dsssb-tgt/` (term 54, taxonomy `exam`, child of `dsssb`).
- They have no post ID, so the app asks for one manually. A typed ID would write to an unrelated **post**.
- The plugin has no term SEO tools (only `get_taxonomies` and `assign_terms`).
- Live today: title comes from the template `%term% Archives %sep% %sitename%` and there is no meta description (term description is empty). The audit warning is real.
- **Tested on the live site (2026-09-26, then reverted):** adding `rank_math_title` and `rank_math_description` as term meta on term 26 changed the live title and description at once. Deleting them restored the page exactly. Cache is not a factor: LiteSpeed Cache is inactive.
- Never worked before: none of the 22 successful deploys in the database was a term page.

### 1.4 Other resolver gaps
- **Duplicate slugs:** `/buy-backlinks/premium-plan/` and `/google-stacking/premium-plan/` share a slug and are rejected as ambiguous, although the REST response includes `link`.
- **Pages with no WordPress object:** post-type archives (`/free-notes/`), `/blog/page/2/`, `?exam=` filter URLs, media files, `/cdn-cgi/`, a homepage that shows a blog roll. They get stored as pages and then prompt for an ID they can never have.
- Some pages we saw (`/paid-marketing/`, `/one-shot-notes/`) are not in REST at all. They may be theme templates or archives.

### 1.5 Site-side notes
- The Notes Factory writes `rank_math_*` through REST via `wp-snippet-rank-math-rest.php`, which registers the fields **for `free_notes` only**. Other post types would silently drop them. Not reviewed: the code that actually pushes notes to WordPress, and whether a republish could overwrite deployed values.
- Known site-tool bugs (from the site's SEO-ARCHIVE.md): `set_option` double-encodes array options, `list_posts` returns 0 for custom post types.

### 1.6 Not the cause
- The rename from claude-wp-mcp to AI SEO Connector. The resolver uses core WordPress REST, not the plugin.

---

## 2. Design principles for the engine
1. **Deployed means live.** Success is decided by reading the public page, not by the plugin's return value.
2. **Right object, right fields.** Every write is addressed by object kind (post or term), taxonomy and ID, and goes through the detected SEO plugin's keys.
3. **Never guess.** If the object can't be determined or the SEO plugin isn't supported, refuse with a specific reason. Never accept a bare numeric ID without a kind.
4. **One interface, many SEO plugins.** Callers use friendly field names. The plugin translates.
5. **Every state is explicit** (ok / no_data / error discipline, extended to deploy): live, saved but not showing, failed, not deployable (with reason).
6. **Know each site before touching it.** Pre-flight checks report SEO plugin, plugin version, caches and page types at connect time.

---

## 3. Plan (each step reviewed and verified before the next; no push without asking)

### Step 1: Stop false success
- After a deploy, fetch the public page and compare the value. Extend the existing post-deploy verification (it already produces `verify_status`).
- New user-visible states: **Live**, **Saved but not showing**, **Failed**, **Not deployable (reason)**.
- Old rows that show "deployed" with `verify=mismatch` or `pending` should display as not live.
- Touches: app deploy route and verification, the deploy UI. Open question: retry and wait time for CDN caches.

### Step 2: Plugin, term support (new plugin version)
- Add `seo_get_term_meta` and `seo_set_term_meta` tools. Rank Math: term meta `rank_math_title`, `rank_math_description` (verified). Yoast: the `wpseo_taxonomy_meta` option (to be verified before shipping).
- Same detection and cache purge as the post tools. Same friendly field names.
- Keep `yoast_set_meta` names working for existing callers. Consider generic aliases (`seo_get_meta`, `seo_set_meta`), since the name is misleading now.
- Tests on a real site (or staging) before release. Tag and release only on request.

### Step 3: Resolver
- After posts, pages and custom post types, look the slug up in each taxonomy REST route (`wp/v2/<rest_base>`).
- Return `kind`, `taxonomy`, `id`, and match on `link` after URL normalisation so duplicate slugs and `//` URLs resolve correctly.
- Return a specific `reason` for pages that can never be deployed (post-type archive, pagination, filter URLs, media, blog-roll homepage).
- Have the crawler skip or flag those URLs instead of prompting.

### Step 4: Database
- Add object type (post or term), and the taxonomy, to the page row; the revision rows need the same so rollback goes to the right place. Migration file to be typed by the user (project rule).

### Step 5: App deploy and rollback routing
- Route by object kind to the post or term tools. A manual ID is only accepted together with a kind. Read the real before-value from the correct object for rollback.

### Step 6: Engine health per site
- Read `/ping` at connect time: plugin version, SEO plugin detected. Warn on outdated plugins and unsupported SEO plugins (All in One SEO, SEOPress are detected but not written).
- Upgrade `vseo.vtraffic.io` to 1.5.0 (or newer).

### Step 7: Repair the past
- Re-deploy the 16 ExamNotesPDF suggestions through the fixed path, or mark them failed. Decide per row after step 1.

---

## 4. Open questions
1. Yoast term storage (`wpseo_taxonomy_meta` option): confirm the exact structure and how to read a before-value for rollback.
2. Should All in One SEO and SEOPress be supported, or refuse with a clear message?
3. How long should the live check wait for caches, and what should the UI say while it waits?
4. Should the Notes Factory push protect fields we deploy (or the reverse)? Needs a look at the publishing code.
5. Which other client sites run Rank Math, and which plugin version does each have?
6. `claude-wp-mcp final`: location unknown; check whether it has anything the current plugin lacks.

---

## 5. Risks and unverified assumptions
- Write path proven live only for **term meta on a Rank Math site**. A **post** write through plugin 1.5.0 has not been tested end to end.
- Yoast behaviour for terms is not tested at all.
- CDN caches (e.g. Cloudflare) are outside the plugin's reach. The live check may show old content for a while.
- Themes or code that override titles by filter would defeat any meta write. Only the live check would catch this.

---

## 6. Plugin UX proposal (input from an outside review, checked against our code)

Source: a design review pasted by the user (not written by us; its reading of the repo is unverified). Direction: reposition the settings page from a "connection utility" to a control center for a **trusted execution agent**: the SEO platform decides what to change, the plugin safely executes and proves it.

### 6.1 Proposals
- **First screen = Connection health**, not API URL and token. Show status, last activity, enabled permissions, detected SEO plugin.
- **Access as four cards** (SEO, Content, Media, Site) instead of four checkboxes.
- **Move API URL, token, Application Password into "Advanced / Connection details".**
- **Rename "Tools"** into Activity, Diagnostics, Security, Advanced. Keep the existing Doctor checks and reposition them.
- **"Last action"** under the status: what was changed, where, when, and whether it was verified.
- **"Change Safety" panel** higher up, and an **AI Actions** section with a lifecycle (recommended, validated, awaiting approval, applied, verified).
- **Visual style:** restrained, WordPress-native, thin borders, small status dots, no gradients, glow, robot imagery or "AI magic" wording. Keep the user-facing name **SEO Connector**.

### 6.2 What our code supports today (checked)
- **Supported now:** connection status, permission groups, token controls, Doctor diagnostics, pause/resume, recent activity, detected SEO plugin. This is a re-layout of `admin/dashboard.php` and `admin/class-admin.php`; no architecture change needed.
- **Not supported: AI Actions / pending approval.** The plugin has no action queue and no approval state. Recommendations and approval live in the SEO platform app. Showing "3 pending" in WordPress needs a new app-to-plugin contract (the app pushes or the plugin pulls a list). **Do not ship a fake or empty version.**
- **Not supported: "Last action" with verification.** The activity log (`AISEOC_Logger`) is a free-text log. Structured entries (tool, object, before/after, verified) are needed first, and verification only exists once step 1 of the plan ships.
- **Careful with the Safety claims:**
  - "Human approval required" is enforced by the SEO platform, not by the plugin. Anyone with the token can write. Word it accordingly, or do not claim it.
  - "WordPress revisions preserved" is true for post content edits but **not** for SEO meta or term meta writes (those create no revisions). Rollback for those depends on our own before-value records.
  - "Changes logged" and "token revocable / connection pausable" are true today.
- This page is a **trust surface**: every claim on it must be literally true.

### 6.3 Suggested order
- **P0 (no new backend):** re-layout with Connection, Access cards, Safety (true claims only), Activity, Diagnostics, Advanced; move the API URL, token and Application Password into Advanced; rename Tools.
- **P1 (needs engine work first):** structured activity log, last action with verification, rollback/change history, then approval and AI Actions via an app-to-plugin contract.
- **Sequencing:** the engine work in sections 3 and 4 comes first, because the UX can only truthfully show "applied and verified" once verification exists.
