# Claude WP Developer Plugin — Setup Guide

For VTechys marketing workers. This explains how to connect a client's
WordPress site to a project in this dashboard, so accepted AI suggestions
(titles, meta descriptions, H1s, canonical URLs, Twitter/OG titles) can be
deployed straight to the live site with one click, instead of copy-pasting
into WordPress by hand.

The connection is made through a WordPress plugin called **Claude WP
Developer** (`claude-wp-mcp`). You install it once on the client's site,
then paste its token into this dashboard.

---

## 1. Download the plugin

Open the project's on-page dashboard, click **WordPress** in the top bar to
open the connection drawer, then click **⬇ Download plugin (.zip)**.

(Direct link if you already know the project: `/downloads/claude-wp-mcp`)

## 2. Install it on the WordPress site

1. In WordPress Admin, go to **Plugins → Add New → Upload Plugin**.
2. Choose the `claude-wp-mcp.zip` you downloaded and click **Install Now**.
3. Click **Activate**.
4. A new **Claude WP Dev** item appears in the WordPress sidebar — open it.
5. Copy the **API token** shown there. This token is generated once on
   activation and only shown in this dashboard screen.

## 3. Connect it in this dashboard

1. Back in the project's on-page dashboard, open the **WordPress** drawer
   again.
2. Fill in:
   - **Site URL** — the client site's full URL, e.g. `https://client-site.com`
   - **API Token** — the token you copied in step 2.5
3. Click **Save connection**, then **Test connection**.
4. A green **verified** badge means the dashboard can now read and write to
   the site. If it fails, double check the URL has no typo and the plugin
   is active on that exact site.

Once verified, the dashboard automatically tries to match every crawled
page on the project to its WordPress post ID in the background, so
individual "Fix on Page" deploys don't need to ask which post a page is.

## 4. Deploying a fix

This part doesn't involve the plugin directly — once a connection is
verified, any **accepted** or **edited** AI suggestion on that project shows
a **Fix on Page** button that writes the new value live and records a
revision you can roll back later. See the
[AI Suggestions & Fix on Page guide](ai-suggestions-user-guide.md) for how
that panel works.

---

## Important — staging vs. production

**Keep this plugin on a development/staging copy of the site whenever
possible, not the live production site.** The plugin's token grants broad
admin-level access (content, Elementor page building, Yoast SEO, media,
plugin installation, and more), and one of its tools (`php_exec`, arbitrary
PHP execution) is a serious capability if the token is ever leaked. It is
disabled by default and requires an explicit confirmation flag to use — do
not enable it unless you specifically need it and understand the risk.

If a client only has one (production) environment, that's fine for normal
use (this dashboard only calls the content/SEO/media tools it needs for
deploys), but treat the API token exactly like a password:

- Never share it outside this dashboard's connection form.
- If you suspect it leaked, regenerate it from the **Claude WP Dev** admin
  screen — this invalidates the old one immediately.
- Removing the plugin (Deactivate + Delete) revokes access entirely.

## What the plugin can do (reference)

The plugin exposes these tool groups over its own REST API — this
dashboard only uses a small slice of them (SEO meta fields, post title, and
post-ID lookup) for deploys, but they're listed here for context on what
the token can reach:

| Group | Examples |
|---|---|
| Content | create/update posts, schedule posts, set featured image |
| SEO (Yoast) | SEO title, meta description, focus keyword, canonical URL, OG/Twitter fields, SEO audit |
| Elementor | build full pages from native widgets, list widgets, set global colors/fonts |
| Media | upload images from a URL |
| Plugins | install a plugin, get/set plugin settings |
| Theme / WordPress core / WooCommerce / Forms / Email / Divi | site-builder and store management tools, not used by this dashboard today |

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| "Connection has not passed a Test Connection check yet" | Click **Test connection** in the drawer at least once after saving. |
| Test connection fails immediately | Plugin not active on that site, or the Site URL has a typo/trailing path. |
| "Could not determine the WordPress post ID for this page automatically" | The page's URL didn't resolve to a known post/page slug — use **Resolve now** in the Fix on Page modal to enter the WordPress post ID manually once; it's remembered after that. |
| Fix on Page deploy fails with a 502 | The live site rejected the write — check the plugin is still active and the token wasn't regenerated since it was saved here. |
