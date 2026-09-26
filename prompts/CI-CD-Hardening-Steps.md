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

### 2c. Stop the old app before running migrations (fixes the lost-migration bug)

**Why (found 2026-09-27 from the deploy logs):** migration 025 printed `Added suggestion_revisions.verify_status` (and two more columns) on the Sep 25 deploy and again on the Sep 26 deploy, yet production lacked the columns afterwards, and a page that read them returned 500. The cause is inferred from that evidence, not reproduced: the migration runs in a *separate* container (`docker compose run --rm`) while the old app container still has the SQLite database open in WAL mode. When the migration's connection closes it cannot checkpoint the write-ahead file into the main database (another connection still holds it), so the change stays in a file inside the migration container, which is deleted when the container exits.

**The fix:** make the migration the only connection to the database. In the `commands = [...]` list, add this line **after** the `docker compose ... build` line and **before** the `for f in /opt/seo-automation/migrations/*.py` loop:

```python
"sudo -u ec2-user docker compose -f /opt/seo-automation/docker-compose.yml stop app",
```

Order after the change: pull, build the new image (the old app is still serving), **stop the old app**, run the migrations, `up -d`. Stopping the old container closes its database connections cleanly, so its work is checkpointed, and the migration then runs alone. Cost: the site is down for the migration plus startup time (seconds to a minute), instead of never.

If you later want zero downtime, the proper fix is to keep the database's `-wal` and `-shm` files on the host (mount the whole data directory instead of one file), so every container shares them. That is a bigger change to `docker-compose.yml` and the database path.

### 2d. Smoke test: does AWS serve this commit, with a complete schema?

Add a new step after "Deploy via SSM". First add a repository variable `APP_URL` (Settings, Secrets and variables, Actions, Variables) with your server address, for example `http://54.80.253.215`:

```yaml
      - name: Confirm the new commit is live and the schema is complete
        run: |
          for i in $(seq 1 20); do
            BODY=$(curl -s --max-time 10 "${{ vars.APP_URL }}/version" || true)
            LIVE=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('commit',''))" 2>/dev/null || true)
            SCHEMA_OK=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('schema',{}).get('ok'))" 2>/dev/null || true)
            echo "attempt $i: live=$LIVE expected=${{ github.sha }} schema_ok=$SCHEMA_OK"
            if [ "$LIVE" = "${{ github.sha }}" ]; then
              [ "$SCHEMA_OK" = "True" ] && exit 0
              echo "::error::The database is missing columns the code expects: $BODY"
              exit 1
            fi
            sleep 6
          done
          echo "::error::The server is not serving commit ${{ github.sha }}"
          exit 1
```

Why the retries: `docker compose up -d` returns before the app has finished starting, so the first calls may fail. That is normal, not an error.

What `schema.ok` means (the app's `/version` now reports it): `true` = every column the models expect exists; `false` = something is missing and `schema.missing` lists it; `null` = the check itself could not run. Only `true` passes.

If a deploy fails on schema drift, repair by running the missing migration inside the **running** container (it shares that container's database state):
`docker compose exec -T app python migrations/<NNN_name>.py`

Verify: after the next merge the run should end green, `curl http://<server>/version` should show the same hash as `git rev-parse origin/main`, and `"schema":{"ok":true,...}`.

Note: `/version` reports the commit only after step 2a exists, and the `schema` field only exists after the pull request that adds it is deployed once. Merge that first, then add 2c and 2d.

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
