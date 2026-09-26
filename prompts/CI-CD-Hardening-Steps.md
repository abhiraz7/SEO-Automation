# CI/CD hardening: make "what is live" provable (you type these)

Why this exists: this week local ran a feature branch, AWS ran `main`, the plugin lived in two repos, and nothing told us what AWS was running. The code half is in the PR (`/version` endpoint, "update available" warning). The pipeline half below is yours to type, because a typo in a workflow can stop deploys. Do them in order; each one is safe on its own.

QA analogy: today the pipeline has no test stage and no smoke test. Steps 1 and 2 add both.

---

## Step 1: run the tests on every pull request (new file, cannot affect production)

Create `.github/workflows/test.yml`:

```yaml
name: Tests

on:
  pull_request:

jobs:
  pytest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run tests
        run: python -m pytest -q
```

Line by line:
- `on: pull_request`: runs when a PR is opened or updated. It does **not** run on `main`, and it never deploys anything.
- `python-version: "3.12"`: `requirements.txt` says Python 3.10 to 3.12 (crawl4ai has no wheel for 3.14). Your local Python is newer; CI should match the Dockerfile (3.12).
- `cache: pip`: reuses downloaded packages, so later runs are faster.
- No `playwright install`: the tests never launch a browser.

Verify: open any PR and the "Tests" check should appear and go green. Then in GitHub, Settings, Branches, add a rule for `main` requiring the "Tests" check before merging. That turns it from advice into a gate.

Already verified for you: the suite passes on a clean checkout with no `.env` (132 tests).

---

## Step 2: make the deploy status truthful, and prove what is live (edit `deploy.yml`)

Two problems in today's file:
1. `aws ssm wait command-executed ... || true` gives up after about 100 seconds. The deploy is usually still running, the status reads "InProgress", and the job goes red although the deploy succeeds. So a red check cannot be trusted.
2. Nothing checks that AWS actually serves the new code.

### 2a. Record the commit for the container (one added line)

The container cannot read git (`.dockerignore` excludes `.git`), so write the commit to a file before the image is built. In the `commands = [...]` list, directly after the `git pull origin main` line, add:

```python
"sudo -u ec2-user bash -c 'git rev-parse HEAD > /opt/seo-automation/.app_commit'",
```

`Dockerfile` has `COPY . .` and `.dockerignore` does not list `.app_commit`, so the file lands inside the image and `/version` reads it. Do not commit `.app_commit` to git (it is written on the server only).

### 2b. Wait for a real final status

Replace the line `aws ssm wait command-executed ... || true` with a polling loop:

```bash
for i in $(seq 1 90); do
  STATUS=$(aws ssm get-command-invocation \
    --command-id "$COMMAND_ID" \
    --instance-id "${{ secrets.EC2_INSTANCE_ID }}" \
    --query "Status" --output text 2>/dev/null || echo "Pending")
  case "$STATUS" in
    Success|Failed|Cancelled|TimedOut) break ;;
  esac
  sleep 10
done
```

Why: it checks every 10 seconds, up to 15 minutes, and stops only at a final state. The existing lines below it (print the output, `exit 1` unless "Success") stay as they are.

### 2c. Smoke test: does AWS serve this commit?

Add a new step after "Deploy via SSM". First add a repository variable `APP_URL` (Settings, Secrets and variables, Actions, Variables) with your server address, for example `http://54.80.253.215`:

```yaml
      - name: Confirm the new commit is live
        run: |
          for i in $(seq 1 20); do
            LIVE=$(curl -s --max-time 10 "${{ vars.APP_URL }}/version" | python3 -c "import sys,json; print(json.load(sys.stdin).get('commit',''))" 2>/dev/null || true)
            echo "attempt $i: live=$LIVE expected=${{ github.sha }}"
            [ "$LIVE" = "${{ github.sha }}" ] && exit 0
            sleep 6
          done
          echo "::error::The server is not serving commit ${{ github.sha }}"
          exit 1
```

Why the retries: `docker compose up -d` returns before the app has finished starting, so the first calls may fail. That is normal, not an error.

Verify: after the next merge the run should end green, and `curl http://<server>/version` should show the same hash as `git rev-parse origin/main`.

Note: `/version` only exists after the PR that adds it is merged and deployed once, so the first deploy after merging will fail step 2c. Merge the PR first, then add 2c. Do 2a and 2b in the same change as the merge.

---

## Step 3: release the plugin automatically on a version bump (plugin repo, later)

Today a release needs a hand-pushed `v*` tag. Goal: raise the version in a PR, merge it, and the release happens. This lives in the plugin repo's `.github/workflows/release.yml`. It is a larger edit (the version check reads the tag name today), so it is deliberately left until steps 1 and 2 are in. One known catch to design around: tags created by a workflow's built-in token do not start other workflows, so the release job must create the tag itself instead of relying on a second workflow.

---

## Local routine (no files to edit)

```powershell
git checkout main
git pull origin main
python -m pytest -q
Get-ChildItem migrations\*.py | ForEach-Object { python $_.FullName }   # AWS runs these on deploy; locally you do it
uvicorn app.main:app --reload
```

Compare environments at any time:

```powershell
curl http://127.0.0.1:8000/version
curl http://<server>/version
```

Same commit means same code. Data is not shared: local and AWS have separate databases.

Rule of thumb: branch from `main`, keep PRs small, and let only a merge to `main` change production.
