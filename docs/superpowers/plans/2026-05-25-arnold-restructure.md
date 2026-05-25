# Arnold Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reshape arnold into a clean three-layer betting-only repo (`frontend/`, `backend/`, `local/`) by deleting dead code, moving the frontend out of the local-client dir, renaming the local-client dir, modernising the backend entrypoint, and upgrading to Python 3.12 — all without breaking prod.

**Architecture:** Five sequential PRs landing on `main`, each independently deployable. Every PR must pass ruff, pytest, frontend build, local smoke (`arnold.bat` boots), and (where backend-touching) post-deploy `/health/ready` + `/health/extraction` green. Each PR respects the 5-minute deploy cooldown.

**Tech Stack:** Python 3.10 → 3.12 (PR 5), FastAPI, SQLAlchemy, Playwright/patchright/camoufox, React 19 + Vite, Docker Compose on Hetzner.

**Reference spec:** [docs/superpowers/specs/2026-05-25-arnold-restructure-design.md](../specs/2026-05-25-arnold-restructure-design.md)

---

## Pre-Flight: Sanity Checks Before Starting

- [ ] **Step 0.1: Confirm clean working tree on main**

```bash
git status
git pull --ff-only origin main
git log -1 --oneline
```
Expected: clean tree, current `main` HEAD (≥ `2d0a44d9`).

- [ ] **Step 0.2: Confirm server is healthy and no deploy in flight**

```bash
ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh status"
ssh root@148.251.40.251 "pgrep -fa 'server-deploy.sh'"
```
Expected: `status` shows healthy containers, no active deploy. `pgrep` returns empty.

- [ ] **Step 0.3: Capture baseline extraction match-rate**

Run via postgres MCP:
```sql
SELECT provider_id,
       SUM(events_matched)::float / NULLIF(SUM(events_processed), 0) AS match_rate
FROM provider_run_metrics
WHERE run_id IN (SELECT id FROM extraction_runs ORDER BY started_at DESC LIMIT 20)
GROUP BY provider_id ORDER BY match_rate DESC;
```
Save the output to a scratchpad — this is the baseline PR 5 must match within ±2%.

---

## File Structure (End State)

After all five PRs:

```
arnold/                          (repo root, unchanged name in git)
├── frontend/                    ← moved in PR 2 (from arnold/frontend/)
│   ├── src/  package.json  vite.config.ts  …
│
├── backend/                     ← unchanged location, internal cleanup only
│   ├── alembic/  alembic.ini
│   ├── pyrightconfig.json
│   ├── src/
│   │   ├── analysis/  api/  bankroll/  config/  constants.py
│   │   ├── core/      db/   matching/  pipeline/  providers/
│   │   ├── services/  repositories/    (kept)
│   │   ├── ml/  risk/  recorders/  jobs/   (kept)
│   │   ├── factory.py  (kept — imported by cli.py)
│   │   └── cli.py     ← renamed from app.py in PR 4
│   ├── tests/
│   └── scripts/
│
├── local/                       ← renamed in PR 3 (from arnold/)
│   ├── launch.py  server.py  proxy.py  http_client.py
│   ├── mirror/                  (browser.py, play_loop.py, workflows/, …)
│   ├── navigations/             ← moved in PR 3 (from root navigations/)
│   ├── data/  tests/
│
├── money/                       ← shared package, stays at repo root
├── docker/  docker-compose.yml  Dockerfile  nginx/
├── scripts/                     ← server-deploy.sh, watchdog, …
├── docs/
├── pyproject.toml               ← STAYS at repo root (Dockerfile builds from root; moving it adds risk for no value)
├── arnold.bat  arnold.ps1       ← launcher names unchanged
└── README.md  CLAUDE.md
```

**Key correction vs the spec:** `pyproject.toml` stays at repo root. The spec showed it under `backend/` to match betty's layout, but arnold's Dockerfile is built from the repo root with `COPY pyproject.toml ./`, and moving it would require Dockerfile rework that adds deploy risk for purely cosmetic gain. The pyproject still gets the betty comment-style refresh in PR 4 — just at its current location.

---

# PR 1: Kill Confirmed-Dead Code

**Goal:** Delete every confirmed-dead file/dir from the spec's kill list. Pure deletes, no moves. Smallest, safest PR.

**Files:**
- Delete: `backend/src/rl/` (entire dir — empty stubs)
- Delete: `arnold/tv_overlay/` (only __pycache__ left)
- Delete: `arnold/tests/test_tv_overlay_router.py`
- Delete: `_raw_utf8.md`
- Delete: `backend/nul`
- Delete: `arnold/debug_screenshot.png`
- Delete: `package.json` (repo root), `node_modules/` (repo root)
- Delete: `arnold/requirements.txt`
- Delete: `docs/tv-overlay-api-audit.md`
- Modify: `.gitignore` (ensure all __pycache__ patterns covered)
- Modify: `.github/workflows/ci.yml` (drop ignore-globs for tests that no longer exist)

### Task 1.1: Verify factory.py is live (do NOT delete)

- [ ] **Step 1.1.1: Confirm factory.py is imported**

```bash
grep -rn "from \.factory\|from src.factory\|import factory" backend/src/ backend/tests/ 2>/dev/null | grep -v __pycache__
```
Expected: at least one match in `backend/src/app.py` (uses `from .factory import ExtractorFactory`).
Action: **factory.py STAYS**. Not on the kill list.

### Task 1.2: Verify root node_modules is safe to remove

- [ ] **Step 1.2.1: Confirm root package.json has no scripts referenced by CI/Dockerfile**

```bash
cat package.json
grep -rln "package.json\|node_modules" .github/ scripts/ Dockerfile docker-compose.yml 2>/dev/null | grep -v arnold/frontend
```
Expected: `package.json` only declares `playwright@^1.58.1` dev dep. Outside-frontend grep returns nothing (or only `.gitignore` mentions). If anything binds a build step to root `node_modules`, **stop and reassess**.

### Task 1.3: Delete dead files in one batch

- [ ] **Step 1.3.1: Delete the kill list**

```bash
git rm -r backend/src/rl/
git rm -r arnold/tv_overlay/
git rm arnold/tests/test_tv_overlay_router.py
git rm _raw_utf8.md
git rm backend/nul
git rm arnold/debug_screenshot.png
git rm package.json
git rm arnold/requirements.txt
git rm docs/tv-overlay-api-audit.md
```

If any of these files aren't tracked (e.g. `backend/nul`, `node_modules/`), use `rm -rf` instead:
```bash
rm -rf node_modules/
rm -f backend/nul
```

- [ ] **Step 1.3.2: Verify no surviving references**

```bash
grep -rn "tv_overlay\|_raw_utf8" backend/ arnold/ tests/ docs/ scripts/ 2>/dev/null | grep -v __pycache__ | grep -v ".git/"
```
Expected: empty (or only matches in the design spec / this plan, which is fine).

### Task 1.4: Clean stale CI test-ignore globs

The CI workflow currently ignores ~18 test patterns that match deleted trading tests. Verify which files still exist and drop the dead ignores.

- [ ] **Step 1.4.1: Find which ignored test patterns still have files**

```bash
for pattern in test_rl_ test_dqn_ test_live_inference test_amt_ test_exchange_stats_ test_fetch_statistics test_swing_ test_market_structure test_betting_models test_level_classifier test_model_serving test_trading_ml test_optimizer test_m10_optimizer test_bet_interceptor test_mirror_ test_mute_notifications test_polymarket_true test_early_exit; do
  found=$(find backend/tests -name "${pattern}*" 2>/dev/null | head -1)
  echo "$pattern: ${found:-DEAD}"
done
```
Note which patterns return DEAD vs files still present.

- [ ] **Step 1.4.2: Edit `.github/workflows/ci.yml`**

For every pattern that returned DEAD in Step 1.4.1, remove the corresponding `--ignore-glob='tests/${pattern}*'` line from the `Run tests` step. Keep `--ignore=tests/providers/test_comeon_dom_parser.py` (per CLAUDE.md, comeon is known-fragile; leave as-is unless that file is also gone).

- [ ] **Step 1.4.3: Verify cleaned CI runs tests locally**

```bash
cd backend
pytest tests/ -q $(grep -- "--ignore" ../.github/workflows/ci.yml | sed 's/^[ \t]*//' | tr '\n' ' ') -x --collect-only 2>&1 | tail -5
```
Expected: `N tests collected` with no errors.

### Task 1.5: Update .gitignore for stale pycaches

- [ ] **Step 1.5.1: Confirm .gitignore covers all __pycache__**

```bash
grep -n "__pycache__\|\.pyc" .gitignore
```
Expected: at least one entry covering `__pycache__/` and `*.pyc`. If missing, add:
```gitignore
__pycache__/
*.pyc
*.pyo
```

- [ ] **Step 1.5.2: Remove any tracked __pycache__ from index**

```bash
git ls-files | grep __pycache__ | head
```
If any are tracked: `git rm -r --cached <paths>`.

### Task 1.6: Verify and commit PR 1

- [ ] **Step 1.6.1: Run full local verification**

```bash
cd backend && ruff check src/ && ruff format --check src/ && pytest tests/ -q -x --ignore-glob='tests/test_rl_*' 2>&1 | tail -10
cd ../arnold/frontend && npm run build 2>&1 | tail -5
```
Expected: ruff clean, pytest green (modulo whatever ignores remain), frontend build succeeds.

- [ ] **Step 1.6.2: Local smoke test**

Boot `arnold.bat`. Verify: tunnel opens, browser opens to working SPA, all three tabs render (Sports / Bankroll / Stats).

- [ ] **Step 1.6.3: Commit and push**

```bash
git status
git commit -m "$(cat <<'EOF'
chore: delete confirmed-dead trading + restructure cruft

- backend/src/rl/ (empty stubs)
- arnold/tv_overlay/ + orphan test
- _raw_utf8.md (7.2 MB trading research dump)
- backend/nul, arnold/debug_screenshot.png
- root package.json + node_modules (leftover monorepo wrapper)
- arnold/requirements.txt (redundant with pyproject.toml)
- docs/tv-overlay-api-audit.md
- prune stale trading-test ignore-globs from ci.yml

Spec: docs/superpowers/specs/2026-05-25-arnold-restructure-design.md
EOF
)"
git push origin main
```

- [ ] **Step 1.6.4: Deploy and verify**

```bash
ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh rebuild backend"
```
Wait for completion, then:
```bash
ssh root@148.251.40.251 "curl -sf http://localhost:8000/health/ready && echo OK"
ssh root@148.251.40.251 "curl -sf http://localhost:8000/health/extraction | python3 -m json.tool | head -30"
```
Expected: `OK` from /health/ready, /health/extraction shows last 3 runs successful.

- [ ] **Step 1.6.5: Verify boot_id and CreatedAt are post-deploy (CLAUDE.md §12)**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && git rev-parse HEAD"
ssh root@148.251.40.251 "curl -s http://localhost:8000/health | python3 -m json.tool | grep boot_id"
ssh root@148.251.40.251 "cd /opt/arnold && docker compose ps backend --format json | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d[\"CreatedAt\"])'"
```
Expected: git HEAD matches your pushed commit, boot_id is new, container CreatedAt is post-deploy.

**WAIT 5 MIN BEFORE STARTING PR 2** (deploy cooldown).

---

# PR 2: Move `arnold/frontend/` → `frontend/`

**Goal:** Frontend lives at repo root, decoupled from the local-client dir.

**Files:**
- Move: `arnold/frontend/` → `frontend/`
- Modify: `arnold/server.py` (line 50: FRONTEND_DIR path)
- Modify: `Dockerfile` (Stage 1: `COPY arnold/frontend ...` → `COPY frontend ...`; if Dockerfile has Stage 1 — verify, may not exist if frontend isn't baked into image)
- Modify: `.github/workflows/ci.yml` (lines 79, 81, 85: `arnold/frontend` paths)
- Modify: `arnold.bat` if it references the frontend dir (verify)
- Modify: `.dockerignore` if it references `arnold/frontend`
- Modify: `frontend/vite.config.ts` if it has any repo-relative paths

### Task 2.1: Audit current frontend references

- [ ] **Step 2.1.1: Find every path-reference to arnold/frontend**

```bash
grep -rn "arnold/frontend\|arnold\\\\frontend" . --include="*.py" --include="*.ts" --include="*.tsx" --include="*.js" --include="*.json" --include="*.yml" --include="*.bat" --include="*.ps1" --include="*.sh" --include="*.toml" --include="Dockerfile" --include="*.md" --include=".dockerignore" --include=".gitignore" 2>/dev/null | grep -v __pycache__ | grep -v node_modules | grep -v "\.git/"
```
Save the output. Every line is a candidate update site.

- [ ] **Step 2.1.2: Inspect current Dockerfile frontend handling**

```bash
grep -n "frontend\|node\|npm\|vite" Dockerfile
```
**Note:** Per CLAUDE.md the Dockerfile is "multi-stage: Stage 1 (Node.js) builds frontend → only `dist/` carried to final image". Verify this by reading the current `Dockerfile`. If Stage 1 is missing (server is API-only and doesn't serve the SPA), there's nothing to update in the Dockerfile here.

### Task 2.2: Move the directory

- [ ] **Step 2.2.1: Move with git**

```bash
git mv arnold/frontend frontend
git status
```
Expected: rename detected for every file in the tree.

### Task 2.3: Update arnold/server.py static-mount path

- [ ] **Step 2.3.1: Update FRONTEND_DIR in arnold/server.py**

Edit `arnold/server.py` line 50:

Before:
```python
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend", "dist")
```

After:
```python
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
```
(One directory up from `arnold/` to reach the new repo-root `frontend/`.)

### Task 2.4: Update Dockerfile (if frontend baked in)

- [ ] **Step 2.4.1: Update Stage 1 COPY paths**

If Dockerfile has a Stage 1 building the frontend, change every `arnold/frontend` reference to `frontend`. If the only frontend reference is something like `COPY arnold/frontend/package*.json ./` or `COPY arnold/frontend/ .`, update those to `frontend/package*.json` / `frontend/`.

If Dockerfile does NOT bake the frontend (sports backend is API-only per CLAUDE.md), skip this task.

### Task 2.5: Update CI workflow

- [ ] **Step 2.5.1: Edit `.github/workflows/ci.yml`**

Change three lines in the `arnold-frontend-typecheck` job:

Before:
```yaml
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: npm
          cache-dependency-path: arnold/frontend/package-lock.json

      - name: Install dependencies
        working-directory: arnold/frontend
        run: npm ci

      - name: TypeScript check
        working-directory: arnold/frontend
        run: npx tsc --noEmit
```

After:
```yaml
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: npm
          cache-dependency-path: frontend/package-lock.json

      - name: Install dependencies
        working-directory: frontend
        run: npm ci

      - name: TypeScript check
        working-directory: frontend
        run: npx tsc --noEmit
```

Optionally rename the job key `arnold-frontend-typecheck` → `frontend-typecheck`.

### Task 2.6: Audit + fix remaining references

- [ ] **Step 2.6.1: Re-run the grep from Step 2.1.1**

Any surviving matches: update each. Common offenders:
- `.dockerignore` may have `arnold/frontend/node_modules` → change to `frontend/node_modules`
- CLAUDE.md mentions `arnold/frontend/` in several places — update those
- `arnold.bat` shouldn't, but verify

### Task 2.7: Verify and commit PR 2

- [ ] **Step 2.7.1: Local frontend build**

```bash
cd frontend && npm run lint && npm run build
ls dist/index.html
```
Expected: lint clean, build succeeds, `dist/index.html` exists.

- [ ] **Step 2.7.2: Backend tests**

```bash
cd ../backend && ruff check src/ && pytest tests/ -q -x 2>&1 | tail -5
```
Expected: green.

- [ ] **Step 2.7.3: Local smoke test**

Boot `arnold.bat`. Open the browser. Verify the SPA loads (not "404 not found" — confirms the new static-mount path works) and all three tabs render.

- [ ] **Step 2.7.4: Commit and push**

```bash
git add -A
git status
git commit -m "$(cat <<'EOF'
refactor: move arnold/frontend → frontend at repo root

Frontend is no longer nested inside the local-client dir.
- git mv arnold/frontend frontend
- arnold/server.py: FRONTEND_DIR points one dir up
- ci.yml: working-directory + cache path updated
- Dockerfile + .dockerignore updated (if applicable)

Step 2 of arnold-restructure plan.
EOF
)"
git push origin main
```

- [ ] **Step 2.7.5: Deploy + verify (only if Dockerfile changed)**

If Dockerfile changed (Stage 1 frontend build):
```bash
ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh rebuild backend"
ssh root@148.251.40.251 "curl -sf http://localhost:8000/health/ready && echo OK"
```

If Dockerfile did NOT change (frontend isn't baked into server image), no backend deploy needed — confirm with:
```bash
git diff --name-only HEAD~1 HEAD | grep -v '^arnold/\|^frontend/\|^\.github/\|^CLAUDE\.md' | head
```
Expected: only frontend/ + arnold/server.py + ci.yml — no backend touched, no deploy required (CLAUDE.md §13).

**WAIT 5 MIN BEFORE PR 3** (only if backend was deployed).

---

# PR 3: Rename `arnold/` → `local/` + Fold `navigations/`

**Goal:** Local-client lives at `local/`. Playwright nav recordings live with the mirror code.

**Files:**
- Move: `arnold/` → `local/`
- Move: `navigations/` → `local/navigations/`
- Modify: 30+ files in `local/mirror/` and `local/tests/` (sed-replace `arnold.http_client` → `local.http_client`, `arnold.mirror.` → `local.mirror.`)
- Modify: `local/server.py` (sys.path manipulation references to `_REPO_ROOT`, package-import comment)
- Modify: `local/launch.py` (working directory comment)
- Modify: `arnold.bat` (path to `local/launch.py`)
- Modify: `arnold.ps1` (same)
- Modify: `kill.bat` if it references `arnold/data/.launch.lock`
- Modify: `CLAUDE.md` (every `arnold/` reference → `local/`)
- Modify: `.gitignore` (any `arnold/data/`, `arnold/.env.local` patterns → `local/`)

### Task 3.1: Pre-audit

- [ ] **Step 3.1.1: List every `from arnold.` import**

```bash
grep -rn "from arnold\.\|import arnold\b" arnold/ tests/ 2>/dev/null | grep -v __pycache__ | wc -l
```
Save the count. After the rename + sed, re-run with `from local.` and confirm same count.

- [ ] **Step 3.1.2: Find every `arnold/` path reference**

```bash
grep -rn "arnold/" arnold.bat arnold.ps1 kill.bat CLAUDE.md .gitignore .dockerignore Dockerfile docker-compose.yml scripts/ 2>/dev/null | grep -v "^arnold/frontend"
```
List of files to touch in this PR.

### Task 3.2: Move the dirs

- [ ] **Step 3.2.1: git mv both dirs**

```bash
git mv arnold local
git mv navigations local/navigations
git status | head -20
```
Expected: rename detected for everything under both moved dirs.

### Task 3.3: Bulk-rewrite imports

- [ ] **Step 3.3.1: Replace `arnold.http_client` → `local.http_client`**

```bash
grep -rln "arnold\.http_client" local/ tests/ 2>/dev/null | grep -v __pycache__ | while read f; do
  sed -i 's/arnold\.http_client/local.http_client/g' "$f"
done
```

- [ ] **Step 3.3.2: Replace `arnold.mirror` → `local.mirror`**

```bash
grep -rln "arnold\.mirror" local/ tests/ 2>/dev/null | grep -v __pycache__ | while read f; do
  sed -i 's/arnold\.mirror/local.mirror/g' "$f"
done
```

- [ ] **Step 3.3.3: Catch any remaining `from arnold.` / `import arnold`**

```bash
grep -rn "from arnold\.\|import arnold\b" local/ tests/ 2>/dev/null | grep -v __pycache__
```
For each remaining match, change `arnold` to `local`. Note: this should NOT match anywhere in `backend/src/` (which has no such imports) — if it does, the spec assumption about layer separation is wrong; stop and investigate.

### Task 3.4: Update local/server.py sys.path manipulation

- [ ] **Step 3.4.1: Update local/server.py lines 25-28 comment**

Edit `local/server.py`:

Before:
```python
# Make `arnold` package importable (parent of this file's directory)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
```

After:
```python
# Make `local` package importable (parent of this file's directory)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
```

(Logic is identical; only the comment changes — the package name `arnold` referenced is now `local`.)

### Task 3.5: Update launcher scripts

- [ ] **Step 3.5.1: Update arnold.bat**

Edit `arnold.bat` lines 7-8, 23:

Before:
```batch
set "LOCKFILE=%~dp0arnold\data\.launch.lock"
if not exist "%~dp0arnold\data" mkdir "%~dp0arnold\data" >nul 2>&1
...
  cd /d "%~dp0arnold"
```

After:
```batch
set "LOCKFILE=%~dp0local\data\.launch.lock"
if not exist "%~dp0local\data" mkdir "%~dp0local\data" >nul 2>&1
...
  cd /d "%~dp0local"
```

- [ ] **Step 3.5.2: Update arnold.ps1 (analogous changes)**

Read `arnold.ps1` and apply the equivalent path replacements (`arnold\data` → `local\data`, `cd ...\arnold` → `cd ...\local`).

- [ ] **Step 3.5.3: Update kill.bat if it references arnold/**

```bash
grep -n "arnold" kill.bat 2>/dev/null
```
For each match (e.g. lockfile path, process-name matching), replace `arnold` → `local` if it refers to the dir, NOT if it refers to the project name in window titles or log prefixes.

### Task 3.6: Update CLAUDE.md

- [ ] **Step 3.6.1: Replace `arnold/` dir references in CLAUDE.md**

The CLAUDE.md has many `arnold/` references that mean the dir (e.g. `arnold/mirror/`, `arnold/frontend/`, `arnold/server.py`, `arnold/launch.py`, `arnold/proxy.py`). These all need `local/`.

But CLAUDE.md ALSO uses `arnold` as the project name (e.g. "Arnold local client", "Arnold - Betting Analytics Platform"). Do NOT touch those.

Suggested approach: open CLAUDE.md, search for `arnold/`, and update each carefully — these are dir paths, not project-name mentions.

Common updates:
- `arnold/frontend/` → `frontend/` (was changed in PR 2; verify spec already reflects this)
- `arnold/mirror/` → `local/mirror/`
- `arnold/server.py` → `local/server.py`
- `arnold/launch.py` → `local/launch.py`
- `arnold/proxy.py` → `local/proxy.py`
- `arnold/data/` → `local/data/`

- [ ] **Step 3.6.2: Update CLAUDE.md §13 (backend-vs-frontend deploy decision)**

The check uses path prefixes. Update:

Before:
```bash
git diff --name-only origin/main...HEAD | grep -v '^arnold/' | head -1
```

After:
```bash
git diff --name-only origin/main...HEAD | grep -v '^local/\|^frontend/' | head -1
```

(Both frontend changes AND local-client changes are now local-only.)

### Task 3.7: Update .gitignore

- [ ] **Step 3.7.1: Replace any arnold/ paths in .gitignore**

```bash
grep -n "arnold/" .gitignore
```
For each, change to `local/` (e.g. `arnold/data/.launch.lock` → `local/data/.launch.lock`).

### Task 3.8: Verify and commit PR 3

- [ ] **Step 3.8.1: Confirm import count is unchanged**

```bash
grep -rn "from local\.\|import local\b" local/ tests/ 2>/dev/null | grep -v __pycache__ | wc -l
```
Expected: matches the count from Step 3.1.1.

- [ ] **Step 3.8.2: Confirm zero surviving `arnold.` package references**

```bash
grep -rn "from arnold\.\|import arnold\b" local/ tests/ 2>/dev/null | grep -v __pycache__
```
Expected: empty.

- [ ] **Step 3.8.3: Run tests**

```bash
cd backend && pytest tests/ -q -x 2>&1 | tail -5
# Local tests run from repo root with PYTHONPATH containing the root:
cd .. && python -m pytest local/tests/ -q -x 2>&1 | tail -5
```
Expected: both green. If `local/tests/` paths break, the test files may need `from local.mirror...` imports — re-run Step 3.3.3.

- [ ] **Step 3.8.4: Local smoke test**

Run `arnold.bat`. Confirm:
1. Lockfile path resolves (no error about missing `local/data/`).
2. `local/launch.py` boots.
3. SSH tunnel opens.
4. Browser opens.
5. All three SPA tabs render.
6. Open a soft-book site (any provider) — confirm balance scrape works (mirror imports are right).

If any provider workflow throws `ModuleNotFoundError: arnold.something`, re-run Step 3.3.3 and look in that workflow's file.

- [ ] **Step 3.8.5: Commit and push**

```bash
git add -A
git status
git commit -m "$(cat <<'EOF'
refactor: rename arnold/ → local/, fold navigations/ under it

Local-client dir is now /local/ to match the frontend/backend/local
three-layer model. Playwright nav recordings live with the mirror code.

- git mv arnold local
- git mv navigations local/navigations
- bulk-rewrite arnold.{http_client,mirror.*} → local.{...} imports
- update arnold.bat / arnold.ps1 / kill.bat to point at local/
- CLAUDE.md dir-path references updated (project name "arnold" kept)

Step 3 of arnold-restructure plan. Local-only — no backend rebuild.
EOF
)"
git push origin main
```

- [ ] **Step 3.8.6: Skip deploy (local-only PR)**

Confirm:
```bash
git diff --name-only HEAD~1 HEAD | grep -v '^local/\|^arnold\.bat\|^arnold\.ps1\|^kill\.bat\|^navigations\b\|^CLAUDE\.md\|^\.gitignore' | head -5
```
Expected: empty. No backend rebuild needed.

---

# PR 4: Rename `app.py` → `cli.py` + Refresh pyproject

**Goal:** Match betty's entrypoint naming convention. Refresh `pyproject.toml` comment style — same deps, cleaner docs.

**Files:**
- Move: `backend/src/app.py` → `backend/src/cli.py`
- Modify: `pyproject.toml` (repo root — betty-style comments, same deps)
- Modify: any caller of `src.app` (Typer entrypoint usage). Verified callers: none in Dockerfile (CMD uses `src.api:app` — FastAPI ASGI app, NOT Typer CLI app). Possible callers in `backend/scripts/` and `backend/run_dev.py`. Sweep first.

**Note:** `src/app.py` is the Typer CLI per CLAUDE.md (`backend/src/app.py — Typer CLI`). The FastAPI ASGI app lives in `src/api/__init__.py`. So the Dockerfile CMD `uvicorn src.api:app` is unaffected by this rename — it references the FastAPI app object, not the Typer CLI module.

### Task 4.1: Sweep src.app callers

- [ ] **Step 4.1.1: Find every caller**

```bash
grep -rn "from src.app\|from src import app\|src\.app:\|src\.app " backend/ scripts/ Dockerfile docker-compose.yml 2>/dev/null | grep -v __pycache__
```
Save the output. Each line is an update site.

- [ ] **Step 4.1.2: Confirm no caller imports the Typer app object specifically**

```bash
grep -rn "from .app import\|from src.app import" backend/src/ 2>/dev/null | grep -v __pycache__
```
If any internal backend code imports `from .app import app` (the Typer object), that import needs renaming too.

### Task 4.2: Rename the file

- [ ] **Step 4.2.1: git mv**

```bash
git mv backend/src/app.py backend/src/cli.py
```

### Task 4.3: Update all callers

- [ ] **Step 4.3.1: Rewrite imports**

For each file from Step 4.1.1:
- `from src.app import ...` → `from src.cli import ...`
- `python -m src.app` → `python -m src.cli`
- `src.app:app` (uvicorn-style, if ANY caller uses Typer-app this way) → `src.cli:app`

- [ ] **Step 4.3.2: Verify nothing references `src.app` anymore**

```bash
grep -rn "src\.app\b\|src/app\.py" . --include="*.py" --include="*.sh" --include="*.bat" --include="*.yml" --include="Dockerfile" --include="*.toml" 2>/dev/null | grep -v __pycache__ | grep -v ".git/" | grep -v "src.api"
```
Expected: empty (filter out `src.api` to avoid false-positive on the FastAPI app).

### Task 4.4: Refresh pyproject.toml comment style

- [ ] **Step 4.4.1: Port betty's commenting style to root pyproject.toml**

Read betty's pyproject for reference:
```bash
gh api repos/blomen/betty/contents/backend/pyproject.toml | python -c "import json,sys,base64; print(base64.b64decode(json.load(sys.stdin)['content']).decode())" | head -120
```

For `pyproject.toml` at repo root, do NOT change:
- `[build-system]` block
- `[project]` name, version, description
- `requires-python` (still `>=3.10` until PR 5)
- The dep list itself

DO change:
- Grouping with section comments (HTTP, Browser automation, Database, Data contracts, etc.)
- Per-dep explanatory comments (one line each, plain English)
- Remove any stale comments referencing trading deps (e.g. "for NQ futures", "Rithmic", etc.)

This is cosmetic — the dependency resolution result should be byte-identical.

### Task 4.5: Verify and commit PR 4

- [ ] **Step 4.5.1: Reinstall in editable mode and run cli**

```bash
pip install -e ".[scrape,dev]"
python -m src.cli --help 2>&1 | head -5
```
Expected: Typer CLI help text prints, no ModuleNotFoundError.

- [ ] **Step 4.5.2: Backend tests + lint**

```bash
cd backend && ruff check src/ && ruff format --check src/ && pytest tests/ -q -x 2>&1 | tail -5
```

- [ ] **Step 4.5.3: Local smoke test**

`arnold.bat` boots; SPA renders. (Same as prior PRs.)

- [ ] **Step 4.5.4: Commit and push**

```bash
git add -A
git status
git commit -m "$(cat <<'EOF'
refactor: rename backend/src/app.py → cli.py (betty convention)

The Typer CLI entrypoint is now named cli.py to match the betty
repo's naming. The FastAPI ASGI app (src.api:app) is unaffected;
Dockerfile CMD unchanged.

Also refreshed pyproject.toml comments to betty style — same deps,
clearer per-dep documentation, dead trading comments dropped.

Step 4 of arnold-restructure plan.
EOF
)"
git push origin main
```

- [ ] **Step 4.5.5: Deploy and verify**

```bash
ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh rebuild backend"
ssh root@148.251.40.251 "curl -sf http://localhost:8000/health/ready && echo OK"
ssh root@148.251.40.251 "curl -sf http://localhost:8000/health/extraction | python3 -m json.tool | head -20"
```

- [ ] **Step 4.5.6: Verify boot_id/CreatedAt fresh (CLAUDE.md §12)**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && git rev-parse HEAD"
ssh root@148.251.40.251 "curl -s http://localhost:8000/health | python3 -m json.tool | grep boot_id"
ssh root@148.251.40.251 "cd /opt/arnold && docker compose ps backend --format json | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d[\"CreatedAt\"])'"
```
Expected: git HEAD matches your pushed commit, boot_id is new (changed since last deploy), container CreatedAt is post-deploy.

**WAIT 5 MIN BEFORE PR 5.**

---

# PR 5: Python 3.10 → 3.12

**Goal:** Match betty's Python version. Final modernisation step.

**Files:**
- Modify: `Dockerfile` (line 2: `FROM python:3.10-slim` → `FROM python:3.12-slim`)
- Modify: `pyproject.toml` (line 9: `requires-python = ">=3.10"` → `">=3.12"`)
- Modify: `.github/workflows/ci.yml` (line ~24: `python-version: "3.10"` → `"3.12"`)
- Modify: `README.md` (any "Python 3.10+" references)
- Modify: `CLAUDE.md` (line 9: "Python 3.10+" → "Python 3.12+")
- Possibly modify: any 3.11/3.12-only syntax flagged by pyright/ruff (rare for a project already on 3.10)

### Task 5.1: Pre-flight — baseline extraction

- [ ] **Step 5.1.1: Capture pre-bump match-rates**

Re-run the postgres query from Step 0.3. Save the latest 20-run match-rate per provider. This is the post-deploy comparison baseline.

### Task 5.2: Update Dockerfile

- [ ] **Step 5.2.1: Edit Dockerfile line 2**

```diff
-FROM python:3.10-slim
+FROM python:3.12-slim
```

### Task 5.3: Update pyproject.toml

- [ ] **Step 5.3.1: Edit pyproject.toml line 9**

```diff
-requires-python = ">=3.10"
+requires-python = ">=3.12"
```

- [ ] **Step 5.3.2: Bump classifiers if present**

```bash
grep -n "Python ::" pyproject.toml
```
If `"Programming Language :: Python :: 3.10"` is listed, change to `3.12`.

### Task 5.4: Update CI workflow

- [ ] **Step 5.4.1: Edit .github/workflows/ci.yml**

```diff
-          python-version: "3.10"
+          python-version: "3.12"
```

### Task 5.5: Update docs

- [ ] **Step 5.5.1: README.md + CLAUDE.md**

```bash
grep -n "3\.10\|3\.11" README.md CLAUDE.md
```
For every match referring to the project's Python version, change to `3.12`.

### Task 5.6: Local validation

- [ ] **Step 5.6.1: Recreate venv with Python 3.12**

If Python 3.12 is installed locally:
```bash
rm -rf .venv
python3.12 -m venv .venv
.venv/Scripts/pip install -e ".[scrape,dev]"
```
Expected: install succeeds; no compilation errors from asyncpg/lightgbm/torch.

- [ ] **Step 5.6.2: Run full backend test suite**

```bash
cd backend && .venv/Scripts/python -m ruff check src/ && .venv/Scripts/python -m pytest tests/ -q -x 2>&1 | tail -10
```
Expected: green. If any tests fail on 3.12 specifically (e.g. removed-in-3.12 stdlib usage), fix inline.

- [ ] **Step 5.6.3: Local smoke test**

`arnold.bat` boots; full mirror flow works for at least one soft provider.

### Task 5.7: Commit and deploy

- [ ] **Step 5.7.1: Commit and push**

```bash
git add -A
git status
git commit -m "$(cat <<'EOF'
chore: upgrade Python 3.10 → 3.12

Matches betty's Python version. Same dep set; no API breakage
expected for asyncpg/playwright/patchright/sqlalchemy/fastapi on 3.12.

- Dockerfile FROM python:3.12-slim
- pyproject.toml requires-python = ">=3.12"
- ci.yml python-version: "3.12"
- README + CLAUDE.md version mentions updated

Final step of arnold-restructure plan.
EOF
)"
git push origin main
```

- [ ] **Step 5.7.2: Deploy (full rebuild — new base image)**

```bash
ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh rebuild backend"
```
This is a LARGER rebuild — the python:3.12-slim base layer is new; all pip installs re-run on top. Expect ~3-5 min.

- [ ] **Step 5.7.3: Post-deploy verification (extended)**

```bash
ssh root@148.251.40.251 "curl -sf http://localhost:8000/health/ready && echo OK"
ssh root@148.251.40.251 "curl -sf http://localhost:8000/health | python3 -m json.tool | grep -E 'boot_id|uptime'"
ssh root@148.251.40.251 "cd /opt/arnold && docker compose ps backend --format json | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d[\"CreatedAt\"])'"
```

- [ ] **Step 5.7.4: Wait for and verify a full extraction cycle**

Wait 10 minutes for the scheduler to run pinnacle + at least one browser-tier provider, then:
```bash
ssh root@148.251.40.251 "curl -sf http://localhost:8000/health/extraction | python3 -m json.tool"
```
Expected: last 3 runs `status: success`, no failed providers, match-rate near baseline.

- [ ] **Step 5.7.5: Compare match-rates vs baseline**

Re-run the postgres query from Step 0.3. Compare to baseline from Step 5.1.1. Per-provider match-rate must be within ±2%. If any provider regressed >2%, investigate before declaring success.

- [ ] **Step 5.7.6: Watch extraction.log for 30 min**

```bash
ssh root@148.251.40.251 "tail -f /opt/arnold/logs/extraction.log" &
# Let it run 30 min, watching for: TypeError, AttributeError, ModuleNotFoundError,
# asyncpg-specific errors, playwright connection failures. Any spike = potential rollback.
```

- [ ] **Step 5.7.7: If anything regresses, ROLLBACK**

```bash
git revert HEAD --no-edit
git push origin main
ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh rebuild backend"
```
Then investigate the regression in isolation.

---

# Post-Restructure: Optional Cleanup

After all 5 PRs land and prod is stable for ~48h:

### Task 6.1: Archive betty repo

- [ ] **Step 6.1.1: Update betty README**

Edit betty/README.md to:
```markdown
# arnold-betting (archived)

Superseded by [blomen/arnold](https://github.com/blomen/arnold) —
the original arnold repo was cleaned up and restructured to match
the layout this repo was scaffolding toward.
```

- [ ] **Step 6.1.2: Archive on GitHub**

```bash
gh repo archive blomen/betty --yes
```

### Task 6.2: Optional GitHub rename of arnold

- [ ] **Step 6.2.1: Rename via gh**

```bash
gh repo rename arnold-betting --repo blomen/arnold
```
GitHub auto-redirects old URLs. Then:

```bash
ssh root@148.251.40.251 "cd /opt/arnold && git remote set-url origin git@github.com:blomen/arnold-betting.git && git remote -v"
```

Update `scripts/server-deploy.sh` if it has a hard-coded remote URL anywhere.

Skip this task entirely if you'd rather keep the existing repo name.

---

## Plan Self-Review Notes

- **Spec coverage:** all 5 PRs from the spec have phases. Optional cleanups (betty archive, GitHub rename) covered in Task 6.
- **Type/method-name consistency:** `from arnold.X` → `from local.X` rewrite is grep+sed driven; no hand-edited type names. `src.app` → `src.cli` is single-symbol rename.
- **No placeholders:** every step has a concrete command or code diff. Steps that look generic ("audit current state") have a specific grep command.
- **One correction vs spec:** spec showed `backend/pyproject.toml` but pyproject stays at repo root in arnold to avoid Dockerfile rework. Plan documents this divergence at the top.
- **Factory.py correction:** spec listed factory.py for verification; sweep confirms it's imported by app.py (now cli.py). Kept.
- **CI test-ignore cleanup:** added to PR 1 as Task 1.4 — not in spec but discovered during plan-writing; the test-ignores reference deleted trading files.

## Estimated Wall-Clock Time

| Phase | Work | Cooldown | Total |
|---|---|---|---|
| PR 1 | 45 min | 5 min | 50 min |
| PR 2 | 90 min | 5 min (if backend rebuilt) | 95 min |
| PR 3 | 2-3 hr | 0 (local-only) | 3 hr |
| PR 4 | 60 min | 5 min | 65 min |
| PR 5 | 90 min + 30 min log-watch | — | 2 hr |

**Total realistic time: ~8 hours of focused work**, spread over 2-3 days to respect cooldowns and let prod settle between major changes.
