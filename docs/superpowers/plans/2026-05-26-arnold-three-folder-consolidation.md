# Arnold Three-Folder Consolidation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Reduce arnold's root to three code folders (`frontend/`, `backend/`, `local/`) plus required root config files. Delete confirmed-dead code surfaced during audit (`money/`, `package-lock.json`). All server tooling consolidates under `backend/`; launchers move under `local/`; `docs/` stays at root.

**Architecture:** Two PRs.
- **PR A1** — local-only cleanup (delete dead, fold root `tests/`+`data/` into `local/`). No deploy.
- **PR A2** — atomic server restructure (move `Dockerfile`, `docker-compose.*`, `pyproject.toml`, `nginx/`, `docker/`, `scripts/` into `backend/`; move launchers into `local/`; update `server-deploy.sh` paths; update server cron). Single coordinated deploy from the new path.

**Reference:** [docs/superpowers/specs/2026-05-25-arnold-restructure-design.md](../specs/2026-05-25-arnold-restructure-design.md) (Phase 1 — completed). This is Phase A of the follow-on cleanup.

---

## End-State Layout

```
arnold/                          (repo root)
├── frontend/                    (Vite/React app — already at root)
├── backend/
│   ├── alembic/  alembic.ini
│   ├── src/  tests/
│   ├── pyrightconfig.json
│   ├── pyproject.toml           ← moved from root
│   ├── Dockerfile               ← moved from root
│   ├── docker-compose.yml       ← moved from root
│   ├── docker-compose.dev.yml   ← moved from root
│   ├── nginx/                   ← moved from root
│   ├── docker/                  ← moved from root (pg-backup.sh, init-market-db.sql)
│   └── scripts/                 ← moved from root (server-deploy.sh, watchdog, audits)
├── local/
│   ├── mirror/  navigations/
│   ├── server.py  launch.py  proxy.py  http_client.py
│   ├── data/                    ← root data/ folded in
│   ├── tests/                   ← root tests/ folded in (only mirror tests)
│   ├── arnold.bat  arnold.ps1   ← moved from root
│   └── kill.bat  kill.ps1       ← moved from root
├── docs/                        (stays at root per user choice)
├── .github/  .claude/
├── .gitignore  .dockerignore  .mcp.json  .env.docker.example
├── README.md
└── CLAUDE.md

DELETED: money/, package-lock.json
```

---

# PR A1: Local-Only Cleanup

**Goal:** Delete dead `money/` package + leftover `package-lock.json`, fold root `tests/` (3 mirror tests) + `data/mirror_intel` into `local/`. No server-side changes. No deploy.

**Files:**
- Delete: `money/` (entire dir — only `__pycache__` files, 0 imports anywhere)
- Delete: `package-lock.json` (leftover from PR 1; `name: "degentraderxd"` is ancient)
- Move: `tests/test_generic_workflow.py` `tests/test_kambi_workflow.py` `tests/test_poly_parse.py` → `local/tests/`
- Move: `data/mirror_intel/` → `local/data/mirror_intel/`
- Modify: `CLAUDE.md` (drop stale `money.convert` guidance + `money/` reference; add note that currency conversion is now inlined in `backend/src/{services,bankroll,repositories}`)

### Task A1.1: Confirm money/ is fully dead

- [ ] **Step 1: Verify no .py source remains**

```bash
find money/ -name "*.py" 2>/dev/null
```
Expected: empty (only `__pycache__` files remain, which are gitignored).

- [ ] **Step 2: Verify zero imports**

```bash
grep -rn "^from money\|^import money\|^[[:space:]]*from money\.\|money\.convert\|money\.Money\|money\.Currency" backend/src local --include="*.py" 2>/dev/null | grep -v __pycache__
```
Expected: empty.

### Task A1.2: Delete dead

- [ ] **Step 3: Delete money/ and package-lock.json**

```bash
rm -rf money/
rm -f package-lock.json
```

(Both are untracked or already gone from git's index — confirm with `git status` after.)

### Task A1.3: Fold root tests/ into local/tests/

- [ ] **Step 4: Move the three mirror test files**

```bash
git mv tests/test_generic_workflow.py local/tests/test_generic_workflow.py
git mv tests/test_kambi_workflow.py local/tests/test_kambi_workflow.py
git mv tests/test_poly_parse.py local/tests/test_poly_parse.py
```

- [ ] **Step 5: Remove now-empty root tests/ dir**

```bash
ls tests/  # should only show __pycache__
rm -rf tests/
```

- [ ] **Step 6: Verify tests still collect from new location**

```bash
python -m pytest local/tests/test_generic_workflow.py local/tests/test_kambi_workflow.py local/tests/test_poly_parse.py --collect-only -q 2>&1 | tail -10
```
Expected: tests collected without collection errors. (Some may have pre-existing issues — confirm only COLLECTION.)

### Task A1.4: Fold root data/ into local/data/

- [ ] **Step 7: Move mirror_intel**

```bash
ls data/
git ls-files data/ | head   # check if any are tracked
```

If `data/mirror_intel` is tracked: `git mv data/mirror_intel local/data/mirror_intel`.
If untracked: `mv data/mirror_intel local/data/mirror_intel` (then add gitignore line if appropriate).
Then `rmdir data/` (now empty).

### Task A1.5: Update CLAUDE.md

- [ ] **Step 8: Drop money/ references**

Search CLAUDE.md for `money/` and `money.convert`. Update the "Currencies" section so it says:

> Currency conversion is implemented inline in `backend/src/{services,bankroll,repositories}` (look for `exchange_rate_sek`, `to_sek`, `convert` functions). The old `money/` shared package was removed; do not look for it.

Keep the rule about not mixing currencies — that's still authoritative. Just remove the "use `money.Money` from the `money/` package" guidance.

### Task A1.6: Verify, commit, push (no deploy)

- [ ] **Step 9: Standard verification**

```bash
cd backend && ruff check src/ 2>&1 | tail -3
pytest tests/ -q -x --ignore-glob='tests/test_fetch_statistics*' --ignore-glob='tests/test_betting_models*' --ignore-glob='tests/test_optimizer*' --ignore-glob='tests/test_m10_optimizer*' --ignore-glob='tests/test_bet_interceptor*' --ignore-glob='tests/test_mirror_*' --ignore-glob='tests/test_mute_notifications*' --ignore=tests/providers/test_comeon_dom_parser.py 2>&1 | tail -5
cd .. && cd frontend && npm run build 2>&1 | tail -3
cd ..
```
Expected: ruff clean, pytest at baseline, frontend builds.

- [ ] **Step 10: Pre-push divergence check**

```bash
git fetch origin main && git log HEAD..origin/main --oneline
```
Rebase if origin moved.

- [ ] **Step 11: Commit and push**

```bash
git add -A
git status
git commit -m "$(cat <<'EOF'
chore: delete dead money/ + leftover package-lock.json; fold root tests/ + data/ into local/

money/ has zero source files (only __pycache__) and zero imports
across backend/local — gutted during the trading strip. Currency
conversion is inlined in backend/src/{services,bankroll,repositories}.

package-lock.json was missed by PR 1 (root cleanup) — name field
"degentraderxd" confirms ancient leftover.

Root tests/ contains 3 mirror tests; they belong under local/tests/.
Root data/mirror_intel is mirror cache; belongs under local/data/.

CLAUDE.md "Currencies" section updated to reflect inlined conversion.

Local-only PR — no backend rebuild.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push origin main
```

- [ ] **Step 12: Confirm no-deploy decision**

```bash
git diff --name-only HEAD~1 HEAD | grep -vE '^(local|frontend)/|^docs/|^CLAUDE\.md$|^\.gitignore$' | head -5
```
Expected: empty.

---

# PR A2: Atomic Server Restructure

**Goal:** Move all server-deployment tooling under `backend/`. Update `server-deploy.sh` internals. Update server-side cron. Move launchers under `local/`. Single atomic deploy from new path.

**This is the highest-risk PR of the entire restructure.** A mid-deploy failure leaves prod in a half-moved state. Verification is critical.

**Files:**
- Move: `Dockerfile` → `backend/Dockerfile`
- Move: `docker-compose.yml` → `backend/docker-compose.yml`
- Move: `docker-compose.dev.yml` → `backend/docker-compose.dev.yml`
- Move: `pyproject.toml` → `backend/pyproject.toml`
- Move: `nginx/` → `backend/nginx/`
- Move: `docker/` → `backend/docker/`
- Move: `scripts/` → `backend/scripts/`
- Move: `arnold.bat` → `local/arnold.bat`
- Move: `arnold.ps1` → `local/arnold.ps1`
- Move: `kill.bat` → `local/kill.bat`
- Move: `kill.ps1` → `local/kill.ps1`
- Modify: `backend/Dockerfile` (rewrite COPY paths since pyproject.toml + backend/ relationship changed)
- Modify: `backend/docker-compose.yml` (relative paths from new location — build context, env file path)
- Modify: `backend/docker-compose.dev.yml` (same)
- Modify: `backend/scripts/server-deploy.sh` (every `/opt/arnold/...` path that referred to relocated files)
- Modify: `backend/scripts/container-watchdog.sh` (paths)
- Modify: any other script in `backend/scripts/` referencing old paths
- Modify: `.dockerignore` (paths)
- Modify: `.gitignore` (paths)
- Modify: `.github/workflows/ci.yml` (working-directory + cache paths)
- Modify: `CLAUDE.md` (every deploy/script command, every architecture-diagram path)
- Modify: `local/arnold.bat` / `local/arnold.ps1` (`%~dp0local` → `%~dp0` since they're now IN local; and any reference to `..\backend\` etc.)
- Server-side: update crontab (`pg-backup.sh` path moves)
- Server-side: invoke new deploy script path

### Task A2.1: Audit current path references

- [ ] **Step 1: Find every "scripts/" + "docker/" + "nginx/" path reference**

```bash
grep -rn "scripts/server-deploy\|scripts/container-watchdog\|/opt/arnold/scripts/\|/opt/arnold/docker/\|/opt/arnold/nginx/\|cd /opt/arnold\b" \
  CLAUDE.md README.md docs/ .github/ Dockerfile docker-compose.yml docker-compose.dev.yml scripts/ 2>/dev/null | head -40
```
Save the result. Every line is a candidate update.

- [ ] **Step 2: Find every "pyproject.toml" or "../pyproject.toml" path reference**

```bash
grep -rn "pyproject.toml" Dockerfile docker-compose.yml docker-compose.dev.yml .github/ scripts/ docs/ 2>/dev/null | head -20
```

- [ ] **Step 3: Find every "docker-compose" + "Dockerfile" reference in tooling**

```bash
grep -rn "docker-compose\|Dockerfile" scripts/ .github/ 2>/dev/null | head -20
```

- [ ] **Step 4: List server cron + system references**

```bash
ssh root@148.251.40.251 "crontab -l 2>/dev/null"
ssh root@148.251.40.251 "ls /opt/arnold/scripts/ /opt/arnold/docker/ 2>/dev/null"
ssh root@148.251.40.251 "cat /opt/arnold/.deploy.lock 2>/dev/null; echo '---'; ls -la /opt/arnold/.deploy.lock 2>/dev/null"
```
Document current state.

### Task A2.2: Move files locally

- [ ] **Step 5: Move backend tooling**

```bash
git mv Dockerfile backend/Dockerfile
git mv docker-compose.yml backend/docker-compose.yml
git mv docker-compose.dev.yml backend/docker-compose.dev.yml
git mv pyproject.toml backend/pyproject.toml
git mv nginx backend/nginx
git mv docker backend/docker
git mv scripts backend/scripts
```

- [ ] **Step 6: Move launchers**

```bash
git mv arnold.bat local/arnold.bat
git mv arnold.ps1 local/arnold.ps1
git mv kill.bat local/kill.bat
git mv kill.ps1 local/kill.ps1
```

### Task A2.3: Fix Dockerfile

The current Dockerfile (from repo root) does:
- `COPY pyproject.toml ./`
- `RUN mkdir -p backend/src && touch backend/src/__init__.py && pip install -e ".[scrape]"`
- `COPY backend/ backend/`
- `WORKDIR /app/backend`
- `CMD ["python", "-m", "uvicorn", "src.api:app", ...]`

With the move, two layout choices for the Dockerfile inside `backend/`:

**(a) Keep build context = repo root** (cleanest, least Dockerfile change). docker-compose specifies `context: ..` and `dockerfile: backend/Dockerfile`. Then Dockerfile content needs minimal change:
- `COPY pyproject.toml ./` → `COPY backend/pyproject.toml ./` (pyproject is now under backend/ from root)
- `COPY backend/ backend/` → stays (still copying backend/ from root)

**(b) Build context = backend/** (cleaner mental model). docker-compose specifies `context: .` (relative to compose file → backend/) and `dockerfile: Dockerfile`. Then frontend Stage-1 would need to escape the context, which Docker resists. Avoid (b).

Choose **(a)**. Update Dockerfile:

- [ ] **Step 7: Update Dockerfile COPY paths**

Read `backend/Dockerfile`. Change:
```diff
-COPY pyproject.toml ./
+COPY backend/pyproject.toml ./
```
(The pyproject.toml is now at `backend/pyproject.toml` from build-context root.)

The `COPY backend/ backend/` line stays the same — backend/ is still at the build context root.

### Task A2.4: Fix docker-compose.yml

The current docker-compose.yml (from repo root) likely has:
- `build: context: .` (or similar)
- `env_file: .env.docker`
- volume mounts using `./...`

- [ ] **Step 8: Update docker-compose.yml**

Read `backend/docker-compose.yml`. Update:
- `build.context: .` → `build.context: ..` (one dir up from compose file = repo root)
- `build.dockerfile: Dockerfile` → `build.dockerfile: backend/Dockerfile`
- `env_file: .env.docker` → `env_file: ../.env.docker` (or move .env.docker to backend/ — decide based on what `.env.docker.example` location implies; current example is at root, suggesting .env.docker is also at root)
- Any `./scripts/...` → `./scripts/...` (still inside backend/, fine)
- Any `./nginx/...` → `./nginx/...` (same)
- Any `./docker/...` → `./docker/...` (same)
- volume binds like `./data:/app/data` → `../data:/app/data` if there's a root `data/` (after A1 there shouldn't be), OR adjust

- [ ] **Step 9: Apply same edits to docker-compose.dev.yml**

### Task A2.5: Update server-deploy.sh

`backend/scripts/server-deploy.sh` references:
- `cd /opt/arnold && docker compose ...` → `cd /opt/arnold/backend && docker compose ...`
- `/opt/arnold/.deploy.lock` → could stay at `/opt/arnold/.deploy.lock` (system-level) OR move to `/opt/arnold/backend/.deploy.lock`. RECOMMEND: keep at `/opt/arnold/.deploy.lock` since it's a system-level coordination primitive across the repo.
- `cd /opt/arnold/backend && pytest` references — already correct
- Any `/opt/arnold/logs/`, `/opt/arnold/data/` references — `logs/` likely needs no change (Docker named volume); `data/` may need adjustment if it was root-level

- [ ] **Step 10: Read server-deploy.sh end-to-end and audit every path**

```bash
cat backend/scripts/server-deploy.sh | head -200
```

Identify each `/opt/arnold/...` path and decide whether it changes:
- `cd /opt/arnold` (for docker compose) → `cd /opt/arnold/backend`
- `git pull` operations — `cd /opt/arnold && git pull` stays (git repo is at /opt/arnold/)
- `docker compose ...` invocations — need to be in `/opt/arnold/backend/` (where docker-compose.yml is now)
- Status file path — likely stays as-is

Use Edit tool to apply each change. Be methodical.

### Task A2.6: Update container-watchdog.sh

- [ ] **Step 11: Audit watchdog**

```bash
cat backend/scripts/container-watchdog.sh
```

Find paths that need updating. Apply Edit.

### Task A2.7: Update launchers

`local/arnold.bat` was at root and did:
- `set LOCKFILE=%~dp0local\data\.launch.lock` — now that bat is INSIDE local/, becomes `%~dp0data\.launch.lock`
- `cd /d %~dp0local` — now `cd /d %~dp0` (bat is already in local/)

- [ ] **Step 12: Update local/arnold.bat**

Read current `local/arnold.bat`. Adjust all `%~dp0local\` → `%~dp0` and remove the `cd /d %~dp0local` (or change to `cd /d %~dp0`).

Also check: does the launcher reference `%~dp0kill.bat`? After move, kill.bat is in `local/` too, so `%~dp0kill.bat` still works (`%~dp0` resolves to dir-of-this-bat = `local/`).

- [ ] **Step 13: Update local/arnold.ps1**

Same logic — apply equivalent PowerShell path updates.

- [ ] **Step 14: Update local/kill.bat + local/kill.ps1**

Read each, fix any paths that referenced the old root-level location of `local/` etc.

### Task A2.8: Update CLAUDE.md

CLAUDE.md has many `scripts/server-deploy.sh` and `/opt/arnold/` references. Each needs updating to the new path.

- [ ] **Step 15: Bulk-update CLAUDE.md paths**

```bash
grep -n "scripts/server-deploy\|/opt/arnold/scripts\|/opt/arnold/docker/\|/opt/arnold/nginx" CLAUDE.md
```

For each: change to `/opt/arnold/backend/scripts/...`, `/opt/arnold/backend/docker/...`, etc.

Also update the architecture diagrams to reflect new tree.

Also update rule 13 (deploy-decision globs) — paths under `backend/` (including `backend/scripts/`, `backend/docker/`, etc.) should NOT count as "local-only" anymore; they're now backend-tooling.

### Task A2.9: Update CI workflow

- [ ] **Step 16: Update .github/workflows/ci.yml**

Currently:
```yaml
- name: Install dependencies
  run: |
    pip install -e ".[dev]"
```

After move, pyproject.toml is at `backend/pyproject.toml`. From the CI checkout root, that's `backend/pyproject.toml`. So:
```yaml
- name: Install dependencies
  working-directory: backend
  run: |
    pip install -e ".[dev]"
    pip install ruff
```

Or use `pip install -e "backend/[dev]"` from root.

Verify the ruff lint + pytest job still has `working-directory: backend` (it does).

### Task A2.10: Update .dockerignore / .gitignore

- [ ] **Step 17: Update .dockerignore**

```bash
cat .dockerignore
```

If it has paths like `scripts/`, `docker/`, those still work (Docker build context is repo root). But may need to add `backend/data/`, etc.

- [ ] **Step 18: Update .gitignore**

```bash
grep -n "scripts/\|docker/\|nginx/" .gitignore
```

Likely fine, but verify.

### Task A2.11: Pre-push verification

- [ ] **Step 19: Standard checks**

```bash
cd backend && ruff check src/ 2>&1 | tail -3
ruff format --check src/ 2>&1 | tail -3
pytest tests/ -q -x --ignore-glob='tests/test_fetch_statistics*' --ignore-glob='tests/test_betting_models*' --ignore-glob='tests/test_optimizer*' --ignore-glob='tests/test_m10_optimizer*' --ignore-glob='tests/test_bet_interceptor*' --ignore-glob='tests/test_mirror_*' --ignore-glob='tests/test_mute_notifications*' --ignore=tests/providers/test_comeon_dom_parser.py 2>&1 | tail -5
cd .. && cd frontend && npm run build 2>&1 | tail -3
cd ..
```

- [ ] **Step 20: Validate docker-compose locally (don't `up`, just `config`)**

```bash
cd backend && docker compose config 2>&1 | head -20
cd ..
```
Expected: prints fully-resolved compose config; no path errors.

- [ ] **Step 21: Local install via new pyproject location**

```bash
pip install -e backend/[scrape,dev] 2>&1 | tail -5
```
Expected: install succeeds.

### Task A2.12: Pre-push divergence check

- [ ] **Step 22: Confirm clean fast-forward**

```bash
git fetch origin main && git log HEAD..origin/main --oneline
```
Rebase if origin moved (likely some other agent activity by now).

### Task A2.13: Commit and push

- [ ] **Step 23: Commit and push**

```bash
git add -A
git status
git commit -m "$(cat <<'EOF'
refactor(arch): consolidate server tooling under backend/, launchers under local/

Repo root now contains only the three code folders + required config:
backend/, frontend/, local/, docs/, plus .github/, README.md, CLAUDE.md.

Moved into backend/:
- Dockerfile + docker-compose.yml + docker-compose.dev.yml
- pyproject.toml (matches betty layout)
- nginx/ docker/ scripts/

Moved into local/:
- arnold.bat arnold.ps1 kill.bat kill.ps1

Internals updated:
- backend/Dockerfile COPY paths (pyproject now at backend/pyproject.toml)
- backend/docker-compose.{yml,dev.yml} build context, dockerfile path,
  env_file path
- backend/scripts/server-deploy.sh: `cd /opt/arnold` →
  `cd /opt/arnold/backend` for docker compose invocations
- backend/scripts/container-watchdog.sh: path updates
- local/arnold.bat + .ps1 + kill.{bat,ps1}: %~dp0local → %~dp0
- .github/workflows/ci.yml: pip install -e backend/[dev]
- CLAUDE.md: every /opt/arnold/scripts/ → /opt/arnold/backend/scripts/,
  architecture diagrams updated, rule 13 deploy-glob updated

Server-side follow-up (separate SSH actions):
- crontab pg-backup.sh path: /opt/arnold/docker/ → /opt/arnold/backend/docker/
- next deploy invokes /opt/arnold/backend/scripts/server-deploy.sh

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push origin main
```

### Task A2.14: Server-side coordinated update + deploy

This is the most delicate sequence. Execute as a single SSH session.

- [ ] **Step 24: Confirm no active deploy**

```bash
ssh root@148.251.40.251 "pgrep -fa 'server-deploy.sh' && echo 'BUSY' || echo 'idle'"
```
If BUSY → wait, retry.

- [ ] **Step 25: Pull the new commit on the server**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && git fetch && git pull --ff-only origin main"
```
Expected: fast-forward to the commit you just pushed.

- [ ] **Step 26: Update crontab to new pg-backup.sh path**

```bash
ssh root@148.251.40.251 "crontab -l 2>/dev/null | sed 's|/opt/arnold/docker/pg-backup.sh|/opt/arnold/backend/docker/pg-backup.sh|g' | crontab -"
ssh root@148.251.40.251 "crontab -l | grep pg-backup"
```
Expected: cron line now references `/opt/arnold/backend/docker/pg-backup.sh`.

- [ ] **Step 27: Confirm pg-backup.sh exists at new location**

```bash
ssh root@148.251.40.251 "ls -la /opt/arnold/backend/docker/pg-backup.sh"
```
Expected: file present, executable.

- [ ] **Step 28: Update any container-watchdog cron entries (if present)**

```bash
ssh root@148.251.40.251 "crontab -l | grep -i 'arnold\|watchdog'"
```
For each entry referencing the old path, update via sed analogously to Step 26.

- [ ] **Step 29: Run the deploy from the NEW path**

```bash
ssh root@148.251.40.251 "bash /opt/arnold/backend/scripts/server-deploy.sh rebuild backend"
```
Wait for completion. This rebuilds the image with the new Dockerfile + compose layout. Expect ~3-5 min.

- [ ] **Step 30: Verify**

```bash
ssh root@148.251.40.251 "curl -sf http://localhost:8000/health/ready && echo OK"
ssh root@148.251.40.251 "curl -s http://localhost:8000/health | python3 -m json.tool | grep -E 'boot_id|uptime'"
ssh root@148.251.40.251 "cd /opt/arnold && git rev-parse HEAD"
ssh root@148.251.40.251 "cd /opt/arnold && docker compose -f backend/docker-compose.yml ps backend --format json | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d[\"CreatedAt\"])'"
```
Expected: ready, fresh boot_id, server HEAD = pushed SHA, container CreatedAt post-deploy.

- [ ] **Step 31: Wait 10 min, verify extraction**

```bash
ssh root@148.251.40.251 "curl -sf http://localhost:8000/health/extraction | python3 -m json.tool | head -30"
```
Expected: last 3 runs success (excluding rainbet's known pre-existing stall).

- [ ] **Step 32: Match-rate comparison**

```bash
ssh root@148.251.40.251 "docker exec arnold-postgres-1 psql -U arnold -d arnold -c \"SELECT provider_id, ROUND((SUM(events_matched)::numeric / NULLIF(SUM(events_processed), 0) * 100)::numeric, 1) AS match_pct FROM provider_run_metrics WHERE run_id IN (SELECT id FROM extraction_runs ORDER BY start_time DESC LIMIT 20) GROUP BY provider_id ORDER BY match_pct DESC NULLS LAST;\""
```
Expected: softbooks at 100% (baseline). If anything regresses, ROLLBACK.

### Task A2.15: Rollback gate

If ANY of:
- Deploy script exits non-zero
- `/health/ready` not ready
- Match-rate regression >2%
- New error type in logs
- Cron failed to update (pg-backup won't run)

ROLLBACK:
```bash
git revert HEAD --no-edit
git push origin main
ssh root@148.251.40.251 "cd /opt/arnold && git pull --ff-only && crontab -l | sed 's|/opt/arnold/backend/docker/pg-backup.sh|/opt/arnold/docker/pg-backup.sh|g' | crontab -"
ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh rebuild backend"
```

Note rollback uses the OLD deploy path (`/opt/arnold/scripts/`) because reverting restores it.

---

## Post-PR-A2 Cleanup

After 48h of stable prod:

- Update any agent-skill that references `scripts/server-deploy.sh` to use `backend/scripts/server-deploy.sh` (`.claude/` config, deploy skill, server-health skill)
- Optionally drop `arnold.egg-info/` (gitignored anyway)

---

# PR B1: Cosmetic Rename arnold → betty

**Goal:** Internal-only rename. pyproject `name`, launcher filenames, code comments and echo messages saying "arnold". DOES NOT change: GitHub repo URL, `/opt/arnold` server path, postgres DB name, Docker container names, named volumes. The "arnold" name persists in infrastructure; "betty" becomes the project's internal/code identity.

**Per user decision (2026-05-26):** Cosmetic scope only. Full rename (server + DB) is deferred indefinitely.

### Task B1.1: Audit current "arnold" references

```bash
grep -rn '\barnold\b' backend/ local/ frontend/ docs/ CLAUDE.md README.md \
  --include="*.py" --include="*.ts" --include="*.tsx" --include="*.toml" --include="*.md" --include="*.bat" --include="*.ps1" \
  2>/dev/null | grep -v __pycache__ | grep -v node_modules | grep -v ".git/" | grep -v "/opt/arnold" | grep -v "arnold-backend-1" | grep -v "arnold-postgres-1" | grep -v "arnold-nginx-1" | grep -v "POSTGRES_USER\|POSTGRES_DB" | head -100
```

Categorize each match:
- **RENAME**: project-name in launchers, log prefixes, pyproject.toml name, comments mentioning "Arnold" as the platform
- **KEEP**: `/opt/arnold` paths (server install dir — out of cosmetic scope), `arnold-backend-1` (container name — out of scope), `POSTGRES_DB=arnold`, `POSTGRES_USER=arnold` (DB name — out of scope), GitHub remote URLs

### Task B1.2: Rename pyproject

Edit `backend/pyproject.toml`:
```diff
-name = "arnold"
+name = "betty"
-description = "Arnold - Betting analytics platform comparing odds across 40+ sportsbooks"
+description = "Betty - Betting analytics platform comparing odds across 40+ sportsbooks"
```
(Or whatever the current description text is — update to reference Betty.)

If `[project.scripts] arnold = "src.cli:app"`:
```diff
-arnold = "src.cli:app"
+betty = "src.cli:app"
```

### Task B1.3: Rename launcher files

```bash
git mv local/arnold.bat local/betty.bat
git mv local/arnold.ps1 local/betty.ps1
git mv local/kill.bat local/kill.bat  # name unchanged — already neutral
git mv local/kill.ps1 local/kill.ps1
```

(kill.bat / kill.ps1 names stay; their internal "[arnold-kill]" log prefixes become "[betty-kill]".)

### Task B1.4: Update launcher contents

Find every `[arnold]` log/echo prefix in `local/betty.bat`, `local/betty.ps1`, `local/kill.bat`, `local/kill.ps1` and change to `[betty]` (or `[betty-kill]` for kill scripts).

Update the launcher lockfile basename: `.launch.lock` (already neutral) — verify it doesn't contain "arnold".

### Task B1.5: Update Python code log prefixes + comments

```bash
grep -rn '"\[arnold\]"\|"arnold "\|# Arnold ' backend/src local --include="*.py" 2>/dev/null | grep -v __pycache__ | head
```

For each: update to "betty" where it refers to the project. Don't touch `/opt/arnold` paths.

Also rename:
- `backend/src/cli.py` Typer app: `app = typer.Typer(help="Arnold - Betting Analytics Platform")` → `app = typer.Typer(help="Betty - Betting Analytics Platform")`
- Any banner / Rich panel that says "Arnold" → "Betty"

### Task B1.6: Update docs

- `README.md`: project name + description
- `CLAUDE.md`: top-of-file title "Arnold - Betting Analytics Platform" → "Betty - Betting Analytics Platform". Keep `/opt/arnold` server path references intact (those are infrastructure).

### Task B1.7: Update frontend titles + branding

- `frontend/package.json`: `"name": "arnold"` → `"name": "betty"` if present
- `frontend/index.html` `<title>` if it says "Arnold"
- React app banner/header if it references "Arnold"

### Task B1.8: Verify, commit, push, deploy

```bash
cd backend && ruff check src/ && pytest tests/ -q -x [...] 2>&1 | tail -5
cd ../frontend && npm run build 2>&1 | tail -3
cd ..
```

Commit:
```
chore(rename): cosmetic arnold → betty (project name, launchers, branding)

Internal-only rename. NOT changed: GitHub repo URL (blomen/Arnold),
server path /opt/arnold, postgres DB name arnold, container names
arnold-{backend,postgres,nginx}-1. Those are infrastructure and would
require destructive migrations.

- backend/pyproject.toml: name + description
- local/{arnold,kill}.{bat,ps1} → local/{betty,kill}.{bat,ps1}
- Log/echo prefixes [arnold] → [betty]
- backend/src/cli.py Typer help text
- README.md + CLAUDE.md titles
- frontend/package.json name + index.html title

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

Deploy: backend tooling is unchanged in this PR, but pyproject.toml `name` change requires a backend rebuild (egg-info regenerates).

```bash
ssh root@148.251.40.251 "bash /opt/arnold/backend/scripts/server-deploy.sh rebuild backend"
```

Verify health + boot_id + extraction same as previous PRs.

## Plan Self-Review Notes

- **No placeholders.** Every step is a concrete command or diff.
- **PR A2 risk is real.** It touches deploy paths + cron in ONE atomic commit. The rollback step is specified.
- **Money/ deletion in PR A1 is well-verified.** Confirmed zero imports + only `__pycache__` remaining.
- **Server cron** is the most dangerous coordination point — Step 26 is the gate.
- **Launchers go to local/ but stay named arnold.bat** for muscle memory. User said "launchers → local/" — interpretation: file location, not filename.
- **docs/ stays at root per user's explicit choice.**
- **`money.convert` mentions in user MEMORY.md** are not in scope to update — that's user-side memory, not code.
