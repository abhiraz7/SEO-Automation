"""
Which commit is this process running?

Exists so "what is live" is provable: the deploy can check AWS's /version
against the commit it just pushed, and locally you can compare your /version
with production's to see at a glance whether the two differ.

Where the commit comes from, in order:
  1. APP_COMMIT environment variable (any host can set it).
  2. A .app_commit file at the repo root. The deploy writes it before building
     the image, because the container cannot read git (.dockerignore excludes
     .git).
  3. `git rev-parse HEAD` in a local checkout (development).
  4. "unknown" -- never an error. A missing commit must not break the app.

Whatever is found is accepted only if it looks like a git hash, so a stray or
malicious value can't be echoed back from this endpoint.
"""
import os
import re
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_COMMIT_FILE = _REPO_ROOT / ".app_commit"
_HASH = re.compile(r"^[0-9a-f]{7,40}$")


def _valid(value: str | None) -> str | None:
    value = (value or "").strip().lower()
    return value if _HASH.match(value) else None


def get_build_info() -> dict:
    env = _valid(os.environ.get("APP_COMMIT"))
    if env:
        return {"commit": env, "source": "env"}

    try:
        from_file = _valid(_COMMIT_FILE.read_text(encoding="utf-8"))
    except OSError:
        from_file = None
    if from_file:
        return {"commit": from_file, "source": "file"}

    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT, capture_output=True, text=True, timeout=3,
        )
        from_git = _valid(out.stdout) if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        from_git = None
    if from_git:
        return {"commit": from_git, "source": "git"}

    return {"commit": "unknown", "source": "none"}
