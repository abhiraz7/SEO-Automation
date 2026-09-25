# AI SEO Connector: rollback runbook

Kept in `prompts/`, not in `ai-seo-connector/`, so it doesn't ship in the plugin zip.
Applies to any release. Written for 1.5.0 (minimal settings screen + the never-released 1.4.0 fixes).

## How updates reach sites (why rollback isn't "undo")

Sites use the bundled update checker against the GitHub repo `abhiraz7/AI-SEO-Connector`. It looks at
the **latest GitHub Release** and offers an update only when that release's version is **higher** than
the installed one. Checks are cached, so a site notices a new release within about 12 hours.

Consequences:
- Sites that already updated to 1.5.0 will **not** be moved back if you delete the release. They stay
  on 1.5.0.
- The only automatic way back is to publish a **higher** version that contains the old code.
- Nothing tells you which sites updated. There is no dashboard for this; assume all connected sites update.

## Before you decide: is it the plugin?

The API (`/tool`, `/mcp`) and the settings screen are separate code paths. A broken settings screen
does not stop the API: a render error is caught and shows a notice, and the platform keeps working.
If only the screen looks wrong, that is a cosmetic fix, not an emergency rollback.

## Option 1: stop it spreading (do this first, takes a minute)

1. GitHub, repo `AI-SEO-Connector` -> Releases -> open `v1.5.0` -> **Delete** (or edit and tick
   "Set as a pre-release" so it is no longer "latest").
2. Sites that have not updated yet will now see the previous release instead (v1.2.2, the last one
   tagged before this) and will not be offered 1.5.0.

This does **not** fix sites that already updated. Continue to Option 2 for them.

## Option 2: roll forward as a new version (the real rollback)

Use a normal revert so history stays honest.

```
git checkout main && git pull
git revert <merge-commit-of-PR-19>        # or revert only the UI commit if the API fixes should stay
# bump Version: header AND AISEOC_VERSION to 1.5.1
# add "## [1.5.1] - <date>" to CHANGELOG.md saying what was reverted and why
git commit -am "Revert 1.5.0 changes, release 1.5.1"
git push origin main
git tag v1.5.1 && git push origin v1.5.1
```

The release workflow refuses to build unless tag, header, `AISEOC_VERSION` and the CHANGELOG heading all
say the same version, so all four must match. Sites offer 1.5.1 within about 12 hours (or immediately via
Dashboard -> Updates -> Check again).

Which commit to revert:
- **Only the screen is bad:** revert just the UI commit ("Simplify settings screen...").
  Keeps the 1.4.0 security and content fixes.
- **The 1.4.0 API changes are the problem** (for example a caller that relied on the old `get_options`
  or on content tools working for private post types): revert the 1.4.0 PRs too, or fix forward.

## Option 3: fix one site by hand, right now

1. Download the zip you want from the repo's Releases page (any old release has `ai-seo-connector.zip`).
2. WP Admin -> Plugins -> Add New -> Upload Plugin -> choose the zip -> when WordPress says the plugin
   already exists, choose **"Replace current with uploaded"**. Or overwrite `wp-content/plugins/ai-seo-connector/`
   over FTP/SFTP.
3. Confirm the version on the Plugins screen.

**Do NOT click Deactivate then Delete.** From 1.4.0 on, deleting the plugin runs `uninstall.php`, which
removes the token, settings, log and legacy `vtseo_*` options. The platform's connection would then break
until you paste a new token. Replacing in place keeps everything.

Rolling back to v1.2.2 or older is safe for settings (the `aiseoc_*` option names are the same). It also
brings back the known bugs 1.4.0 fixed (title-only updates can strip iframes from post content).

## After any rollback

- The platform itself still calls the legacy `vtseo/v1` route. Every version keeps that alias; don't
  remove it in a rollback.
- Check one site: Plugins screen shows the expected version, and the settings screen shows "Connected"
  after the platform's next request.
- Write the reason into `CHANGELOG.md` and the AgentLog.

## Not verified

Written from how the update checker and the release workflow are configured, not from a live rollback.
Nobody has yet rolled a connected site back with this procedure.
