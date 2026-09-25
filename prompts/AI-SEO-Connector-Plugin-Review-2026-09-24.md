# AI SEO Connector (WordPress plugin) — Code Review & Reference — 2026-09-24

Purpose: a complete snapshot of how the `ai-seo-connector/` plugin works, how the Python
platform uses it, and every issue found in a full read-through — so the next session (human
or agent) can start work immediately without re-reading all ~2,700 lines of plugin code.

**Scope reviewed:** plugin v1.2.2 (commit `c94cb5d`, branch `feature/ai-seo-connector-plugin`).
Every non-vendored file read in full: `ai-seo-connector.php`, `includes/*`, `mcp-handlers/*`,
`admin/*`, `.github/workflows/release.yml`, `README.md`, `CHANGELOG.md`, `.gitattributes`.
Vendored `vendor/plugin-update-checker/` (YahnisElsts PUC v5p7) was NOT reviewed.
Platform side: `app/wordpress.py` and `app/routes/wordpress.py` call sites.

**Verification status — read this first:** static review only. There is **no PHP CLI on this
machine** (`php -l` unavailable), and nothing was run against a live WordPress install.
Findings are marked with a confidence level. No code was changed in this session.

> **ADDENDUM (later 2026-09-24, v1.3.0 work) -- supersedes the "static only / no PHP CLI" caveat above.**
> A PHP CLI DOES exist on this machine: the Local app bundles PHP 8.2 at
> `C:\Users\Abhinav\AppData\Roaming\Local\lightning-services\php-8.2.30+1\bin\win64\php.exe`
> (not on PATH). All 13 non-vendored plugin files pass `php -l`. A throwaway WordPress 7.1.2 test bed
> (private mysqld on a scratch port, PHP built-in server, Playwright/Chromium screenshots) was built
> and torn down; nothing touched the ExamNotesPDF Local site. Live results:
> - **PASS live:** legacy `vtseo_*` -> `aiseoc_*` migration keeps the SAME token (first real proof);
>   fresh install generates a new 64-char token; `/ping` works on both `aiseoc/v1` and legacy
>   `vtseo/v1`; 401/403/503 error codes as documented; Doctor self-test does not count as contact;
>   RankMath 1.0.279 get/set incl. noindex/nofollow merge; Yoast get/set.
> - **Finding #1 CONFIRMED LIVE:** `update_post {title}` strips an `<iframe>` from `post_content`.
> - **Finding #6 CONFIRMED LIVE:** RankMath `noindex:"false"` (string) sets noindex.
> - **Finding #4 (masked errors) CONFIRMED LIVE:** a disabled tool group returns a generic HTTP 500
>   "Tool execution failed"; the real message ("Tool group 'content' is disabled...") only reaches the log.
> - **NEW #9 (availability):** `json_decode( get_option('aiseoc_allowed_actions') )` is unguarded in
>   `class-router.php` (~line 93) and `class-mcp.php` (~178, 221, 480). If the option is ever stored as
>   an ARRAY (import/migration tool, `wp option add --format=json`) every API call fatals with a
>   TypeError. Migrated legacy values are strings, so low likelihood, but the blast radius is the whole
>   API. Fix: one shared `allowed_actions(): array` accessor that tolerates both shapes.
> - **NEW #10 (availability, static read, not reproduced):** the per-IP rate limiter runs BEFORE the
>   token check, so a correct token is rejected (429) while its source IP is limited. Behind a
>   proxy/CDN `REMOTE_ADDR` is shared, so scanner noise could lock the platform out. Fix: compare the
>   token first; only throttle failed attempts.
> - v1.3.0 (UI rebuild, live status, Doctor) also partially addresses #5: the settings-screen SEO group
>   label is now "SEO"/Yoast-or-RankMath. The MCP `instructions` string in `class-mcp.php` (~478) still
>   says RankMath is unsupported and is still stale.
> - Bug found and fixed during verification: Idle status text read "No contact for 1 week ago".
> - Still open from the work order: #1, #2, #3, #4 (honest errors, uninstall behavior), #6, #7, #8.
> - Trust-copy honesty: the README/UI copy was rewritten so it no longer claims "Site info is read-only"
>   or that uninstall removes data (neither was true). Trust copy still says tool calls are limited to
>   enabled groups -- true for `/tool` and MCP `tools/call`, NOT for MCP `resources/*` (#2).

> ## RESOLUTION STATUS (end of 2026-09-24 session) -- read this before the findings below
> Findings were logged as public GitHub issues on `abhiraz7/AI-SEO-Connector` (#4-#9) and fixed
> one PR per issue, each reproduced on a throwaway WordPress 7.1.2 before the change and re-tested
> after. All six issues are CLOSED; PRs #10-#17 are merged to `main`. **v1.4.0 is prepared
> (version bump + CHANGELOG entry, PR #17) but NOT tagged** -- connected sites only update when a
> `v1.4.0` tag is pushed (latest release is still v1.2.2). Tagging is the user's call.
>
> | Original finding (numbers in this doc) | GitHub issue / PR | Result |
> |---|---|---|
> | #1 kses strips iframes/scripts on title-only `update_post` | issue #4 / PR #11 | FIXED, confirmed before+after. New `AISEOC_Content::save_post()` lifts kses for the save and restores it with `kses_init()` |
> | Bonus found while testing: `schedule_post` on a draft published it immediately | issue #4 / PR #11 | FIXED (needs `edit_date => true`); was also broken on the old code |
> | "Yoast indexables may not refresh rendered `<title>`" | issue #4 / PR #11 | NOT a bug: Yoast 28.5 renders the new title/description right after `yoast_set_meta`. Not re-tested on RankMath's rendered output |
> | Page caches not purged / `flush_cache` only object cache | issue #5 / PR #12 | FIXED via new `AISEOC_Cache` (WP Rocket, LiteSpeed, W3TC, WP Super Cache, WP Fastest Cache, SiteGround). Tested with recording stubs of each plugin's purge API, NOT the real plugins. CDN/Cloudflare not reachable |
> | #2 MCP resources bypass groups / any post type / unfiltered meta | issue #6 / PR #13 | FIXED |
> | #3 `get_options` deny-list | issue #6 / PR #13 | FIXED: allow-list of 9 keys (`READABLE_OPTION_KEYS`) |
> | #8 unauthenticated failed-auth DB writes, and NEW #10 rate limit ran before the token check | issue #6 / PR #13 | FIXED: credentials checked first, failures only counted, one "Rate limit hit" log per window |
> | NEW #9 unguarded `json_decode(get_option('aiseoc_allowed_actions'))` | issue #6 / PR #13 | FIXED: `AISEOC_Router::allowed_groups()` accepts string or array |
> | Hardening: arbitrary meta keys / post types / author IDs | issue #6 / PR #13 | FIXED for content tools (blocked meta prefixes, public post types only, author must exist) |
> | #4 no uninstall cleanup + generic 500s | issue #7 / PR #14 | FIXED: `uninstall.php`, `includes/legacy-options.php` (shared by migration + uninstall), `AISEOC_Router::describe_error()` (400/403 real message, 500 generic) |
> | #5 stale Yoast-only wording / incomplete `yoast_set_meta` schema | issue #7 / PR #14 | FIXED (tool names `yoast_*` kept for compatibility) |
> | #6 RankMath `noindex:"false"` = ON | issue #8 / PR #15 | FIXED: real boolean parsing before any write; Yoast keeps native '2' |
> | Retired sitemap ping | issue #8 / PR #15 | REMOVED (tool count 23 -> 22). IndexNow not built: needs a key file at the site root and doesn't cover Google |
> | Audit miscounts non-ASCII text | issue #8 / PR #15 | FIXED: `mb_*`, Unicode word count (`\p{M}` keeps Devanagari vowel signs), plain text, score counts warnings, `score_percent`/`word_count` added |
> | #7 GET /mcp holds a worker | issue #9 / PR #16 | FIXED: 405 + `Allow: POST` (a filter corrects WP's Allow header) |
> | release.yml version check; tool list x3; dead `log_level`; JSON-RPC notifications | issue #9 / PR #16 | FIXED: workflow checks tag vs header vs `AISEOC_VERSION` vs CHANGELOG and uses the CHANGELOG entry as the release body (shell step tested locally; the workflow itself has NOT run on GitHub yet); single `AISEOC_Router::registry()`; `log_level` removed; notifications get no reply |
> | v1.3.0 (UI, status, Doctor) that had sat uncommitted | PR #10 | SHIPPED to `main` first so the fixes build on it |
>
> **Still open / not done**
> - SSRF hardening in `handler-media.php` (IPv4-only `gethostbyname`, DNS-rebinding gap) -- mitigated by WP core's `wp_safe_remote_get`; not changed.
> - Legacy `vtseo_*` options are still copied and never deleted on upgrade (only removed by uninstall).
> - `uninstall.php` multisite branch is untested (single-site only).
> - Real cache plugins and RankMath front-end output were not tested.
> - Platform (`app/wordpress.py`) still calls the legacy `vtseo/v1` namespace, so that alias must stay. Verified end to end against the fixed plugin with the platform's own code: ping, get_options homepage lookup, get_post, get/set Yoast meta, H1 title update, alt text by id and by url, rollback -- all OK; the platform's 28 mocked-HTTP tests pass.
> - The monorepo copy `ai-seo-connector/` is BEHIND the plugin repo `main` (it holds the uncommitted 1.3.0 work only). Syncing it overwrites uncommitted files, so it was left for the user to approve.
>
> **Behaviour changes callers can notice (all in the 1.4.0 CHANGELOG):** `get_options` allow-list; content tools limited to public post types; blocked meta keys; MCP resources follow group settings; `yoast_sitemap_ping` gone; 400/403 error codes; GET /mcp 405; notifications unanswered; audit `score` semantics.
>
> ### Test bed recipe (rebuilt and deleted this session; nothing of it remains)
> Needs only what Local already installs: PHP 8.2 at `C:\Users\Abhinav\AppData\Roaming\Local\lightning-services\php-8.2.30+1\bin\win64\php.exe` (no php.ini -- write one with `extension_dir` + mysqli/curl/openssl/mbstring/fileinfo/zip/intl/gd) and MySQL at `...\lightning-services\mysql-8.0.35+4\bin\win64\bin\mysqld.exe`.
> 1. `mysqld --no-defaults --initialize-insecure --datadir=<dir>`, then run it with `--port=3399 --bind-address=127.0.0.1 --mysqlx=OFF`; create the DB with Local's `mysql.exe` (WP-CLI `db create` / `db query` need a MySQL client on PATH).
> 2. Download WordPress with `curl -L https://wordpress.org/latest.zip` + `unzip` (WP-CLI `core download` fails: no `tar` on PATH). WP-CLI: `wp-cli.phar` from raw.githubusercontent.com/wp-cli/builds. Use a SHORT install path (e.g. `C:\Users\Abhinav\.aiseoc-tb`); the deep Temp path breaks archive extraction. Set `TMP` to it.
> 3. `php -S 127.0.0.1:8899 -t <wp> router.php` where router.php serves real files and otherwise sets `SCRIPT_NAME=/index.php` and requires WordPress's index.php. Install with `--url=http://127.0.0.1:8899`, then `rewrite structure '/%postname%/'` and `rewrite flush`, with `MSYS_NO_PATHCONV=1` (Git Bash otherwise rewrites `/%postname%/` into a Windows path).
> 4. Install Yoast (`wordpress-seo`) and RankMath (`seo-by-rank-math`) with `wp plugin install`; only one SEO plugin can be active at a time, so test each provider in turn.
> 5. Copy the plugin folder into `wp-content/plugins/ai-seo-connector` (copy untracked files too) and activate. Read the token with `wp option get aiseoc_api_token` BEFORE adding any mu-plugin that prints output.
> 6. Non-ASCII payloads: send JSON from UTF-8 FILES (`curl --data-binary @file`); Windows console arguments turn Hindi into `?`. Admin AJAX test: log in with curl + a cookie jar, read the nonce from the settings page (`const nonce = "..."`).
> 7. Tear down: stop the php and mysqld processes (by port), delete the install folder. Do not touch Local's own sites.

**Why this doc lives in `prompts/` and NOT inside `ai-seo-connector/`:** that folder is
mirrored to the public GitHub repo `abhiraz7/AI-SEO-Connector`, and `release.yml` zips the
whole folder (minus `.git*`, composer files, node_modules) into the client-facing plugin zip.
A list of the plugin's security weaknesses must never ship to client sites or a public repo.

---

## 1. Architecture at a glance

```
Python platform (app/wordpress.py)
   │  Authorization: Bearer <token>
   │  POST {site}/wp-json/vtseo/v1/tool   body: {"tool": "...", "params": {...}}
   ▼
AISEOC_Auth::permission_callback      includes/class-auth.php
   │  enabled? → rate-limited? → Bearer (hash_equals) or Basic (WP App Password)
   ▼
AISEOC_Router::dispatch_tool → call_tool     includes/class-router.php
   │  get_handlers(): tool → [Class, method, group];  group must be in aiseoc_allowed_actions
   ▼
AISEOC_Content / AISEOC_SEO / AISEOC_Media / AISEOC_Site     mcp-handlers/*.php
   (thin wrappers over WP core: wp_insert_post, update_post_meta, media_handle_sideload…)
```

Second entry point: `/mcp` (JSON-RPC 2.0, MCP "Streamable HTTP", protocol `2024-11-05`) in
`includes/class-mcp.php` → `tools/call` reuses the same `AISEOC_Router::call_tool`. Also
implements `resources/*` and `prompts/*`, which do NOT go through `call_tool` (see finding #2).

Mental model: same registry pattern as the platform's `app/jobs/registry.py` — a name→handler
map plus one dispatcher. QA analogy: `/tool` and `/mcp` are two test runners over one suite.

### Files (non-vendored, line counts)
| File | Lines | Role |
|---|---|---|
| `ai-seo-connector.php` | 150 | Header (v1.2.2), requires, legacy option migration, activation defaults, boot hooks, PUC auto-update |
| `includes/class-auth.php` | 155 | Permission callback, token regen, App Password creation, per-IP rate limit |
| `includes/class-router.php` | 193 | REST route registration (2 namespaces), `/tool` dispatcher, handler map, tool manifest |
| `includes/class-mcp.php` | 644 | MCP JSON-RPC: initialize, tools/list, tools/call, resources, prompts, SSE, tool input schemas |
| `includes/class-logger.php` | 41 | Activity log in `aiseoc_activity_log` option, max 200 entries |
| `mcp-handlers/handler-content.php` | 260 | Posts CRUD, schedule, featured image, taxonomies |
| `mcp-handlers/handler-seo.php` | 330 | Yoast/RankMath get/set meta, audit, sitemap ping |
| `mcp-handlers/handler-media.php` | 266 | Upload (URL/base64), list/get/delete, alt text by ID / by URL, SSRF + file-type guards |
| `mcp-handlers/handler-site.php` | 110 | Site info, list plugins, get_options (deny-list), flush object cache |
| `admin/class-admin.php` | 131 | Admin menu, 4 AJAX actions (nonce + manage_options), accent colors by WP admin scheme |
| `admin/dashboard.php` | 345 | Settings screen HTML/CSS/JS (trust card, token, App Password, groups, activity log) |
| `.github/workflows/release.yml` | 52 | On tag `v*`: rsync → zip `ai-seo-connector/` → GitHub Release asset |

### REST endpoints (registered under BOTH `aiseoc/v1` and legacy `vtseo/v1`)
| Route | Method | Handler |
|---|---|---|
| `/ping` | GET | status, plugin, version, site url, time |
| `/capabilities` | GET | `tool_manifest()` (names/groups/descriptions) |
| `/tool` | POST | `{tool, params}` dispatcher |
| `/logs` | GET | last 50 activity log entries |
| `/mcp` | POST | JSON-RPC (single or batch); SSE response if `Accept: text/event-stream` |
| `/mcp` | GET | SSE keep-alive `: ping` every 15 s for up to 300 s |

All use `AISEOC_Auth::permission_callback`.

### wp_options keys the plugin owns
| Key | Content |
|---|---|
| `aiseoc_api_token` | 64-hex Bearer token (`bin2hex(random_bytes(32))`), set by `add_option` on activation |
| `aiseoc_enabled` | `'1'` / `'0'` |
| `aiseoc_log_level` | always `'info'` — saved but **never read** anywhere |
| `aiseoc_allowed_actions` | JSON string, default `["content","seo","media","site"]` |
| `aiseoc_app_username` | login of the admin who created the App Password (password itself never stored) |
| `aiseoc_activity_log` | array of `{time, level, message}`, max 200, autoload=false |
| `aiseoc_rl_<md5(ip)>` | transient, failed-auth counter, 900 s |
| legacy `vtseo_*` | copied to `aiseoc_*` by `aiseoc_migrate_legacy_options()` (runs on activation AND every `plugins_loaded`); old values are never deleted |

### Tool inventory (23 tools, 4 groups) — ★ = actually called by the platform today
| Group | Tools |
|---|---|
| content | create_post, ★update_post, ★get_post, list_posts, delete_post, schedule_post, set_featured_image, get_taxonomies, assign_terms |
| seo | ★yoast_get_meta, ★yoast_set_meta, yoast_audit, yoast_sitemap_ping (the `yoast_*` names are historical — get/set now route to Yoast or RankMath) |
| media | upload_media, list_media, get_media, delete_media, ★update_media_meta, ★update_media_alt_by_url |
| site | get_site_info, list_plugins, ★get_options, flush_cache |

The tool→group mapping is **duplicated in 3 places** and must be kept in sync by hand:
`class-router.php::get_handlers()`, `class-router.php::tool_manifest()`,
`class-mcp.php::handle_tools_list()` `$group_map` (plus schemas in `tool_definitions()`).

### SEO provider switch (`handler-seo.php`)
`active_provider()`: `WPSEO_VERSION` defined → yoast; else `RANK_MATH_VERSION` → rankmath;
else yoast (fallback so calls never hard-fail). Checked per call, not cached. Both return the
same friendly keys (`seo_title`, `meta_description`, `focus_keyword`, `canonical_url`,
`og_*`, `twitter_*`, `noindex`, `nofollow`, `is_cornerstone`, `schema_*`, `primary_category`,
`raw`, `seo_plugin`). RankMath stores robots as one array `rank_math_robots`; set merges flags.
RankMath has no equivalent for `schema_article_type`/`schema_page_type` → returned empty,
no-op on set. `raw` writes allowed only for keys prefixed `_yoast_` / `rank_math_`.
CHANGELOG notes RankMath is **untested against a live RankMath install**.

### Auth details (`class-auth.php`)
- Order: disabled check (503) → rate-limit check (429) → header missing (401) → Bearer / Basic → else 401.
- Bearer: `hash_equals(stored, substr(header, 7))`; fail → 403 + counter++; success clears counter.
- Basic: relies on WP core's Application Password auth having already set the user;
  requires `is_user_logged_in() && current_user_can('manage_options')`.
- **Bearer auth never calls `wp_set_current_user()`** → every Bearer request runs as user 0
  (root cause of finding #1; also means `create_post` defaults `post_author` to 0).
- Rate limit: 20 failures / 15 min / `REMOTE_ADDR`. Behind a proxy/CDN, `REMOTE_ADDR` is the
  proxy, so the limit is shared by everyone behind it.
- App Password creation temporarily forces `wp_is_application_passwords_available*` true
  (WP disables them on non-SSL sites), deletes old ones named "AI SEO Connector" or
  "VtechSEO Agent", returns `{username, password, encoded}` once.

### Release / update pipeline
- Plugin source is a **copy** inside this monorepo (not a submodule — `git rev-parse` shows
  the monorepo root) and separately pushed to `github.com/abhiraz7/AI-SEO-Connector`
  (commit `13bde12` "Sync monorepo copy…"). Two copies = drift risk; the plugin's
  `.github/workflows/release.yml` only runs in the plugin's own repo.
- Tag `vX.Y.Z` in the plugin repo → workflow builds `ai-seo-connector.zip` → GitHub Release.
- Sites update via vendored PUC: `buildUpdateChecker(repo, __FILE__, 'ai-seo-connector')`,
  `setBranch('main')`, `enableReleaseAssets()`. Guarded by `file_exists` so a missing vendor dir
  can't fatal.
- License switched to Proprietary in v1.2.2 (CHANGELOG records this as a deliberate departure
  from the WP GPL norm).

### How the platform integrates (`app/wordpress.py`, `app/routes/wordpress.py`)
- `_TOOL_PATH = "/wp-json/vtseo/v1/tool"` and `test_connection` hits `/wp-json/vtseo/v1/ping`
  — **the platform still uses the legacy namespace.** Removing the `vtseo/v1` alias (as the
  router comment suggests) would break every deploy until the platform is re-pointed to `aiseoc/v1`.
- `app/wordpress.py` docstrings still say "VtechSEO Agent" / "claude-wp-mcp" and "not verified
  against a live site" — naming is stale after the rename.
- Homepage resolution: `get_options {"keys": ["show_on_front","page_on_front"]}`.
- `FIELD_DEPLOYERS` in `app/routes/wordpress.py`: meta_description, title, twitter, canonical,
  opengraph → `yoast_set_meta`; **h1 → `update_post` (title)**. Before-value read via
  `yoast_get_meta` / `get_post` so rollback writes the real prior value.
- Alt text: `update_media_meta {media_id, alt}` or `update_media_alt_by_url {url, alt}`.
- Tests: `tests/test_wordpress.py`, `tests/test_field_deployers.py` (mocked HTTP only; no PHP tests exist).
- Live sites connected: `examnotespdf.in`, `vseo.vtraffic.io` (per migration docblock).

---

## 2. Findings (ranked most serious first)

Each: what's wrong → why it matters → proposed fix → confidence.

### 🔴 #1 — Title/H1 fix silently strips HTML from the post body
- **Where:** `includes/class-auth.php:41-53` (Bearer never sets a user) + `mcp-handlers/handler-content.php:56-84` (`update_post`).
- **What:** Bearer requests run as user 0, who lacks `unfiltered_html`, so WP's `kses_init`
  installs `content_save_pre → wp_filter_post_kses`. `wp_update_post` merges the **entire
  existing post** and re-saves it through `wp_insert_post` → `sanitize_post(..., 'db')`, so the
  existing `post_content` gets kses-filtered even when only `title` was sent.
- **Impact:** the platform's `h1` deployer (`update_post {title}`) can remove `<iframe>`,
  `<script>`, `<style>`, form inputs, custom-HTML-block embeds from a client page while
  reporting success. Violates the "a fix never looks like something it isn't" contract. Only
  finding that can corrupt client content today.
- **Proposed fix:** in `update_post` (and `create_post`), call `kses_remove_filters()` before
  `wp_update_post`, re-add with `kses_init_filters()` in a `finally`, and keep explicit
  `wp_kses_post()` only on the fields the caller actually passed. (Alternative considered:
  `wp_set_current_user()` to the configuring admin on Bearer auth — broader behavior change,
  also affects authorship/capabilities; not preferred as first fix.)
- **Confidence:** high from WP core behavior; **not reproduced live**. Repro to do: page with a
  Custom HTML block containing an `<iframe>` → deploy an H1 fix → diff `post_content`.

### 🔴 #2 — MCP resources bypass tool-group toggles and the meta filter
- **Where:** `includes/class-mcp.php:247-368` (`handle_resources_list`, `handle_resource_read`).
- **What:** neither checks `aiseoc_allowed_actions` — disabling "Content" still exposes all
  posts. `resources/read` accepts **any post ID of any status/post type** (drafts, private,
  password-protected, `shop_order`, changesets…) and returns **raw, unfiltered** post meta
  (skips `safe_post_meta()` which `get_post` uses).
- **Related:** `get_post` tool also has no post-type restriction, and `safe_post_meta()`'s
  prefix list (`_stripe_`, `_paypal_`, `_wc_`, `_edd_`, `_password`, `_auth_`, `session_`,
  `_transient_`, `auth_key`) does not cover WooCommerce's `_billing_*` / `_shipping_*` keys.
  On legacy (non-HPOS) WooCommerce storage, order PII is readable.
- **Proposed fix:** gate resources on the `content`/`media` groups; restrict to
  `post`/`page`/`attachment` (public post types); run meta through `safe_post_meta` (make it
  shared); add a post-type allow-list to `get_post` too.
- **Confidence:** high (direct code read).

### 🟠 #3 — `get_options` uses an incomplete deny-list
- **Where:** `mcp-handlers/handler-site.php:18-30, 80-92`.
- **What:** blocks salts, siteurl/home/admin_email, roles, own token keys. Does NOT block
  `mailserver_pass`, SMTP plugin passwords, other plugins' API keys/licenses,
  `aiseoc_activity_log`, `aiseoc_app_username`, `active_plugins`. The tool description at
  `class-router.php:189` **claims `active_plugins` is blocked — it isn't** (doc/code mismatch).
- **Proposed fix:** switch to an allow-list. The platform only needs `show_on_front`,
  `page_on_front` (maybe `blogname`, `blogdescription`, `page_for_posts`, `permalink_structure`).
  QA analogy: deny-list = asserting against known-bad values; allow-list = asserting the exact expected set.
- **Confidence:** high.

### 🟠 #4 — README / trust copy makes claims the code doesn't back
- No `uninstall.php` and no `register_uninstall_hook` → deleting the plugin **leaves the token,
  settings and log in `wp_options`**. README "Uninstall entirely" section says data is removed.
- README "Every response is a clear success or a clear, specific error" — but
  `class-router.php:99-103` turns every non-`InvalidArgumentException` (incl. "group disabled"
  `RuntimeException` and "post_id required") into a generic 500 "Tool execution failed".
  Meanwhile MCP `tools/call` (`class-mcp.php:234-239`) returns the raw `$e->getMessage()` —
  inconsistent between the two entry points.
- Trust card calls Site info "read-only" but `flush_cache` is a write (harmless, but imprecise).
- **Proposed fix:** add `uninstall.php` deleting `aiseoc_*` + legacy `vtseo_*` options and
  `aiseoc_rl_*` transients; introduce a user-facing exception type (or reuse
  `InvalidArgumentException`/`RuntimeException`) returned as 400/403 with the real message;
  align MCP to the same policy.
- Per memory `project_wp_plugin_trust_surface`, this screen/README is trust-critical.

### 🟠 #5 — Stale "Yoast-only" wording after RankMath support (v1.2.0)
- `class-mcp.php:478` (MCP `instructions` to AI clients): "RankMath fields are not yet
  supported" — false; LLM clients will avoid the SEO tools on RankMath sites.
- `admin/dashboard.php:12` group label "SEO (Yoast)" and description "Read/write Yoast SEO meta".
- Tool descriptions in manifest/schemas: "Get all Yoast SEO meta…".
- `yoast_set_meta` MCP schema (`class-mcp.php:580-592`) omits `og_image`, `twitter_image`,
  `is_cornerstone`, `primary_category`, `schema_*`, `raw` that the handler supports.
- **Fix:** copy/schema edits only; keep tool names `yoast_*` for backward compatibility.

### 🟡 #6 — RankMath treats `noindex: "false"` as ON
- **Where:** `handler-seo.php:216-225` uses PHP truthiness; `"false"` and `"no"` are truthy.
  Schema types noindex/nofollow as **string**. An LLM sending `"false"` would add noindex
  (de-index the page). Yoast path writes the literal string (Yoast expects `'1'` noindex,
  `'2'` index, `''`/`'0'` default), so semantics differ per provider.
- **Fix:** normalize with `filter_var($v, FILTER_VALIDATE_BOOLEAN, FILTER_NULL_ON_FAILURE)`,
  reject unparseable values; map to Yoast `'1'`/`'2'` consistently; change schema to boolean.

### 🟡 #7 — `GET /mcp` SSE ties up a PHP worker for 5 minutes
- **Where:** `class-mcp.php:94-111`. Each authenticated GET holds a PHP-FPM worker with
  `sleep(15)` loops for 300 s. On shared hosting a handful exhausts workers → whole site slow/down.
  Nothing in the platform uses it. MCP spec allows `405 Method Not Allowed` for GET.
- **Fix:** return 405 from `handle_get` (or remove the GET route).

### 🟡 #8 — Unauthenticated requests cause unbounded DB writes; log races
- **Where:** `class-logger.php:7-18`, called from `class-auth.php:30, 49, 63`.
- Every failed auth AND every request after the rate limit trips ("Rate limit hit") does a full
  read-modify-write `update_option` of a ≤200-entry array. No token needed → write amplification.
  Concurrent requests lose log entries (non-atomic).
- **Fix:** don't log per request once rate-limited (log once per window), or aggregate counts;
  consider a custom table or transient-based buffer later.

### Needs live verification (not confirmed bugs)
- **Yoast indexables:** `yoast_set_meta` writes post meta directly via `update_post_meta`.
  Yoast 14+ renders from its `wp_yoast_indexable` table; it is **unverified** whether a direct
  meta write refreshes the indexable (so the rendered `<title>`/description change) without a
  post save. Directly tied to the open handover question "has a live deploy + rollback ever
  been verified?" Test on `vseo.vtraffic.io`: deploy title → view source → rollback → view source.
  Possible fix if stale: trigger `wp_update_post(['ID'=>$id])` / Yoast's indexable builder after set.
- **Page caches:** `flush_cache` only calls `wp_cache_flush()` (object cache). WP Rocket,
  LiteSpeed, W3TC, Cloudflare page caches will still serve the old HTML. MCP instructions
  promise fixes become "visible immediately" — overstated. Could add `clean_post_cache($id)` +
  known cache-plugin purge hooks (`rocket_clean_post`, `litespeed_purge_post`, …).

### Minor / housekeeping
- `yoast_sitemap_ping` (`handler-seo.php:312-329`): Google's and Bing's sitemap ping endpoints
  are retired; also hardcodes `/sitemap_index.xml` (Yoast/RankMath), ignores core `wp-sitemap.xml`.
- `yoast_audit` (`handler-seo.php:243-309`): `strlen` not `mb_strlen` (miscounts non-ASCII, e.g.
  Hindi content); `str_word_count` ignores non-Latin scripts; `strpos` keyword match is
  case-folded with `strtolower` not `mb_strtolower`; score formula `passes/(passes+errors)`
  ignores warnings.
- `create_post`/`update_post` accept arbitrary `meta` keys (only `sanitize_key`), arbitrary
  `post_type`, arbitrary `author_id`, and work on attachment IDs (can rewrite
  `_wp_attached_file` etc.; WP ≥4.9.9 `wp_delete_file_from_directory` limits the deletion
  impact). Consider a meta-key allow-list / block of `_wp_*` keys.
- JSON-RPC: notifications to known methods (e.g. `ping` without `id`) still get a response —
  should return nothing. `PROTOCOL_VERSION` ignores the client's requested version (acceptable).
- SSRF check in `handler-media.php:213-237`: `gethostbyname` is IPv4-only and there is a
  DNS-rebinding TOCTOU gap before `download_url`; mitigated by WP core's `download_url` →
  `wp_safe_remote_get` (`reject_unsafe_urls`). Low.
- Uploads rely on block-lists for extension/MIME; real protection is WP core's allowed-mimes
  check in `media_handle_sideload` / `wp_upload_bits`. Fine, but don't loosen upload_mimes.
- Legacy `vtseo_*` options are copied, never deleted → if the old `vtechseo-agent` plugin is
  reactivated, the old token works again. If old + new plugins are both active, both register
  `vtseo/v1` routes (later one wins + `_doing_it_wrong`). Rollout step: deactivate/delete old plugin.
- `aiseoc_log_level` setting is saved (always `'info'`) but the logger never filters by level — dead setting.
- `release.yml` does not verify tag == plugin header `Version`/`AISEOC_VERSION`. A mismatch
  makes PUC show "update available" forever. Add a check step (grep header, compare to `${GITHUB_REF_NAME#v}`).
- Plugin version lives in 2 places (`Version:` header and `AISEOC_VERSION`) — bump both.
- `admin/dashboard.php` exposes the full token in `data-token` on the settings page HTML
  (admins only, `manage_options`) — acceptable, noted.
- CHANGELOG notes a few hardcoded glow colors not migrated to the accent variables; scheme
  accents visually unverified except default "Fresh".

---

## 3. What's solid (don't regress these)
- Bearer compare via `hash_equals` (constant time).
- Application Password raw value never persisted; old "VtechSEO Agent" app passwords cleaned up.
- Legacy migration uses strict `false`/`''` checks so `aiseoc_enabled = '0'` isn't re-migrated (well commented).
- `add_option` on activation → updates/reactivation never regenerate the token.
- All admin AJAX: `check_ajax_referer('aiseoc_nonce')` + `manage_options`.
- Media: SSRF guard + blocked extensions/MIME + finfo sniff on base64, on top of WP core checks.
- `update_media_alt_by_url` fails loudly when the URL isn't a library attachment (no fake success).
- Logger tolerates the log option being stored as a JSON string (PHP 8 fatal avoided).
- Dashboard render wrapped in try/catch so a UI error never takes down the API.
- Group gating in `call_tool` applies to both `/tool` and MCP `tools/call`.

---

## 4. Suggested work order (one task at a time → user verifies → push, per task-list workflow)
1. **#1 kses stripping** in `update_post`/`create_post` — small, highest impact.
2. **#2 + #3** — gate/restrict MCP resources + `get_post` post types; `get_options` allow-list.
3. **#4 + #5** — `uninstall.php`, honest error responses, README/dashboard/MCP-instructions copy, schema completeness.
4. **Live verification on `vseo.vtraffic.io`** — H1 kses repro, Yoast indexable refresh, page-cache visibility, deploy + rollback end-to-end.
5. #6, #7, #8, then housekeeping (version check in release.yml, retire sitemap ping, mb_* in audit, de-duplicate the group map).
6. Later: re-point platform to `aiseoc/v1`, then retire the `vtseo/v1` alias and legacy option copies.

Rules that apply when doing this work (from CLAUDE.md / memory): state the fix and wait for
confirmation before editing; mentor mode (explain the concept + review like a PR); never push
to `main` proactively (push = prod deploy); commits carry the `Co-Authored-By: Claude` trailer;
plugin changes must be synced to BOTH the monorepo copy and the `AI-SEO-Connector` repo, and
bump `Version:` + `AISEOC_VERSION` + CHANGELOG together.
