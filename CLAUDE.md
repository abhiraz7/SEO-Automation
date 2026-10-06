# Instructions for Claude Code

## Review like an experienced dev
- Before/after any code change, review it the way a senior engineer reviews their own diff before opening a PR: list every file and function touched, why each one needed to change, and any ripple effects (callers, templates, tests, other providers following the same pattern) — don't just show the new code in isolation.
- Call out anything risky, unverified, or assumption-based explicitly (e.g. "not tested against a live site" the way `wordpress.py`'s docstrings already do) rather than presenting it as done.

## Bug fixes ship with a regression test
- Every bug fix includes a test that reproduces the bug, in the same commit/PR as the fix. No test, not done.
- Write the test FIRST and run it against the unfixed code: it must fail, and fail for the reason in the bug report (not an import error or a typo). Then fix, and watch the same test pass. A test that was never red proves nothing — same as an automation check that can't fail.
- Reproduce the exact scenario from the report (the real payload shape, the real input, the real DB state), not a generic happy path.
- Start the test's docstring with `Regression:` and say what broke and what the user saw — follow `tests/test_deploy_status.py` and `tests/test_dataforseo_payload_shape.py`.
- Put it in the existing test file for that module; create a new file only if none fits.
- If the bug was a silent or unlogged failure, also assert the logging with `caplog`.
- If a bug genuinely can't be tested in pytest (live third-party API behaviour, deploy/infra, browser-only UI), say so explicitly in the review and state how it was verified instead. Never skip the test silently.
- Run the full suite (`python -m pytest -q`) before calling the fix done, and report the result.
- This applies to any agent or session picking up work on this repo, not just the current conversation.
